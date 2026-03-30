from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config
from .content_fetcher import fetch_article_or_text
from .formatting import (
    ARTICLE_FALLBACK_SUMMARY,
    COMMENTS_FALLBACK_SUMMARY,
    format_article_message,
    format_comments_message,
)
from .hn_client import fetch_comments_text, fetch_front_page_posts
from .models import FrontPagePost, PostRecord
from .storage import Storage
from .summarizer import GeminiClient, GeminiDailyQuotaExceededError
from .telegram import TelegramClient

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CycleStats:
    frontpage_posts_seen: int = 0
    qualifying_posts: int = 0
    processed_posts: int = 0
    initial_publications: int = 0
    comments_updates: int = 0
    skipped_comment_updates: int = 0
    failures: int = 0
    gemini_calls: int = 0


class PollingService:
    def __init__(
        self,
        config: Config,
        storage: Storage,
        gemini_client: GeminiClient,
        telegram_client: TelegramClient,
    ) -> None:
        self.config = config
        self.storage = storage
        self.gemini_client = gemini_client
        self.telegram_client = telegram_client
        self.gemini_daily_quota_exhausted = False

    def run_cycle(self) -> None:
        self.storage.initialize()
        self.gemini_daily_quota_exhausted = False
        stats = CycleStats()
        usage_before = self.storage.get_gemini_usage_totals()
        gemini_calls_before = self.storage.get_gemini_call_count()
        LOGGER.info("Gemini usage before cycle: %s", _format_usage_log(usage_before))
        posts = fetch_front_page_posts(self.config.request_timeout_seconds)
        stats.frontpage_posts_seen = len(posts)
        active_ids = {post.hn_id for post in posts}
        self.storage.mark_missing_posts_inactive(active_ids)

        for post in posts:
            if post.score < self.config.hn_min_points:
                continue
            stats.qualifying_posts += 1
            try:
                self._process_post(post, stats)
            except Exception:
                stats.failures += 1
                LOGGER.exception("Post processing failed for hn_id=%s", post.hn_id)
        usage_after = self.storage.get_gemini_usage_totals()
        gemini_calls_after = self.storage.get_gemini_call_count()
        stats.gemini_calls = gemini_calls_after - gemini_calls_before
        LOGGER.info("Gemini usage after cycle: %s", _format_usage_log(usage_after))
        LOGGER.info(
            "Gemini usage delta for cycle: %s",
            _format_usage_log(_usage_delta(usage_before, usage_after)),
        )
        LOGGER.info("Cycle summary: %s", _format_cycle_stats(stats))

    def _process_post(self, post: FrontPagePost, stats: CycleStats) -> None:
        stats.processed_posts += 1
        record = self.storage.upsert_post(post)
        if record.article_message_id is None or record.comments_message_id is None:
            self._publish_initial_messages(post, record)
            stats.initial_publications += 1
            return
        latest_comment_summary = self.storage.get_latest_comment_summary(post.hn_id)
        if latest_comment_summary is None:
            self._publish_initial_messages(post, record)
            stats.initial_publications += 1
            return
        last_comment_count = int(latest_comment_summary["comment_count"])
        if not should_refresh_comments(
            current_comment_count=post.comment_count,
            last_summarized_comment_count=last_comment_count,
            threshold=self.config.comment_resummary_threshold,
            updates_done=record.comment_update_count,
            max_updates=self.config.max_comment_updates_per_post,
        ):
            stats.skipped_comment_updates += 1
            LOGGER.info("Skipping comment refresh for hn_id=%s", post.hn_id)
            return
        if self._refresh_comments_message(post, record):
            stats.comments_updates += 1
        else:
            stats.skipped_comment_updates += 1

    def _publish_initial_messages(self, post: FrontPagePost, record: PostRecord) -> None:
        article_summary = self._generate_article_summary(post)
        article_message_id = record.article_message_id
        if article_message_id is None:
            article_message_id = self.telegram_client.send_message(
                format_article_message(
                    post,
                    article_summary,
                    max_chars=self.config.telegram_max_message_chars,
                )
            )
            self.storage.set_article_message_id(post.hn_id, article_message_id)
            LOGGER.info("Published article message for hn_id=%s", post.hn_id)

        comments_summary, comment_tree_hash, _ = self._generate_comments_summary(post)
        comments_message_id = record.comments_message_id
        if comments_message_id is None:
            comments_message_id = self.telegram_client.send_message(
                format_comments_message(
                    post,
                    comments_summary,
                    max_chars=self.config.telegram_max_message_chars,
                )
            )
            self.storage.set_comments_message_id(post.hn_id, comments_message_id)
            LOGGER.info("Published comments message for hn_id=%s", post.hn_id)
        self.storage.store_comment_summary(
            post.hn_id,
            comment_tree_hash=comment_tree_hash,
            comment_count=post.comment_count,
            model_name=self.config.gemini_model,
            summary_text=comments_summary,
        )

    def _refresh_comments_message(self, post: FrontPagePost, record: PostRecord) -> bool:
        comments_summary, comment_tree_hash, used_fallback = self._generate_comments_summary(post)
        if used_fallback:
            LOGGER.info(
                "Skipping comments message update for hn_id=%s because comment regeneration fell back.",
                post.hn_id,
            )
            return False
        if record.comments_message_id is None:
            raise RuntimeError(
                "Cannot refresh comments message without a stored Telegram message ID."
            )
        self.telegram_client.edit_message(
            record.comments_message_id,
            format_comments_message(
                post,
                comments_summary,
                max_chars=self.config.telegram_max_message_chars,
            ),
        )
        self.storage.store_comment_summary(
            post.hn_id,
            comment_tree_hash=comment_tree_hash,
            comment_count=post.comment_count,
            model_name=self.config.gemini_model,
            summary_text=comments_summary,
        )
        self.storage.increment_comment_update_count(post.hn_id)
        LOGGER.info("Updated comments message for hn_id=%s", post.hn_id)
        return True

    def _generate_article_summary(self, post: FrontPagePost) -> str:
        latest = self.storage.get_latest_article_summary(post.hn_id)
        fetch_result = fetch_article_or_text(
            url=post.url,
            fallback_text=post.text,
            timeout_seconds=self.config.request_timeout_seconds,
            max_chars=self.config.article_max_chars,
        )
        self.storage.store_article_fetch(
            post.hn_id,
            fetch_method=fetch_result.fetch_method,
            source_url=fetch_result.source_url,
            raw_content=fetch_result.raw_content,
            gemini_input_text=fetch_result.gemini_input_text,
            content_hash=fetch_result.content_hash,
            error_message=fetch_result.error_message,
        )
        if fetch_result.content and latest and latest["content_hash"] == fetch_result.content_hash:
            LOGGER.info("Reusing cached article summary for hn_id=%s", post.hn_id)
            return str(latest["summary_text"])
        if not fetch_result.content:
            return self._generate_article_summary_from_url_fallback(
                post, local_fetch_error=fetch_result.error_message
            )
        if self.gemini_daily_quota_exhausted:
            summary = ARTICLE_FALLBACK_SUMMARY
            self.storage.store_article_summary(
                post.hn_id, fetch_result.content_hash, self.config.gemini_model, summary
            )
            return summary
        try:
            response = self.gemini_client.summarize_article(
                post.title,
                fetch_result.source_url,
                fetch_result.content,
                max_chars=self.config.article_summary_max_chars,
            )
            self.storage.store_gemini_call(
                hn_id=post.hn_id,
                operation="article_summary",
                model_name=self.config.gemini_model,
                response_id=response.response_id,
                usage=response.usage,
            )
            summary = response.text
        except GeminiDailyQuotaExceededError:
            self._mark_gemini_daily_quota_exhausted(post.hn_id)
            summary = ARTICLE_FALLBACK_SUMMARY
        except Exception:
            LOGGER.exception("Article summary generation failed for hn_id=%s", post.hn_id)
            summary = ARTICLE_FALLBACK_SUMMARY
        self.storage.store_article_summary(
            post.hn_id, fetch_result.content_hash, self.config.gemini_model, summary
        )
        return summary

    def _generate_article_summary_from_url_fallback(
        self, post: FrontPagePost, local_fetch_error: str | None
    ) -> str:
        if not post.url:
            summary = ARTICLE_FALLBACK_SUMMARY
            self.storage.store_article_summary(post.hn_id, None, self.config.gemini_model, summary)
            return summary
        self.storage.store_article_fetch(
            post.hn_id,
            fetch_method="gemini_url_context",
            source_url=post.url,
            raw_content=None,
            gemini_input_text=f"URL_CONTEXT: {post.url}",
            content_hash=None,
            error_message=local_fetch_error,
        )
        if self.gemini_daily_quota_exhausted:
            summary = ARTICLE_FALLBACK_SUMMARY
            self.storage.store_article_summary(post.hn_id, None, self.config.gemini_model, summary)
            return summary
        try:
            response = self.gemini_client.summarize_article_from_url(
                post.title,
                post.url,
                max_chars=self.config.article_summary_max_chars,
            )
            self.storage.store_gemini_call(
                hn_id=post.hn_id,
                operation="article_url_context_fallback",
                model_name=self.config.gemini_model,
                response_id=response.response_id,
                usage=response.usage,
            )
            summary = response.text
        except GeminiDailyQuotaExceededError:
            self._mark_gemini_daily_quota_exhausted(post.hn_id)
            summary = ARTICLE_FALLBACK_SUMMARY
        except Exception:
            LOGGER.exception("Gemini URL-context article summary failed for hn_id=%s", post.hn_id)
            summary = ARTICLE_FALLBACK_SUMMARY
        self.storage.store_article_summary(post.hn_id, None, self.config.gemini_model, summary)
        return summary

    def _generate_comments_summary(self, post: FrontPagePost) -> tuple[str, str, bool]:
        comments_text, tree_hash = fetch_comments_text(
            item_id=post.hn_id,
            timeout_seconds=self.config.request_timeout_seconds,
            max_chars=self.config.comments_max_chars,
        )
        latest = self.storage.get_latest_comment_summary(post.hn_id)
        if comments_text and latest and latest["comment_tree_hash"] == tree_hash:
            LOGGER.info("Reusing cached comments summary for hn_id=%s", post.hn_id)
            return str(latest["summary_text"]), tree_hash, False
        if not comments_text:
            return COMMENTS_FALLBACK_SUMMARY, tree_hash, True
        if self.gemini_daily_quota_exhausted:
            return COMMENTS_FALLBACK_SUMMARY, tree_hash, True
        try:
            response = self.gemini_client.summarize_comments(
                post.title,
                comments_text,
                max_chars=self.config.comments_summary_max_chars,
            )
            self.storage.store_gemini_call(
                hn_id=post.hn_id,
                operation="comments_summary",
                model_name=self.config.gemini_model,
                response_id=response.response_id,
                usage=response.usage,
            )
            summary = response.text
            used_fallback = False
        except GeminiDailyQuotaExceededError:
            self._mark_gemini_daily_quota_exhausted(post.hn_id)
            summary = COMMENTS_FALLBACK_SUMMARY
            used_fallback = True
        except Exception:
            LOGGER.exception("Comments summary generation failed for hn_id=%s", post.hn_id)
            summary = COMMENTS_FALLBACK_SUMMARY
            used_fallback = True
        return summary, tree_hash, used_fallback

    def _mark_gemini_daily_quota_exhausted(self, hn_id: int) -> None:
        if self.gemini_daily_quota_exhausted:
            return
        self.gemini_daily_quota_exhausted = True
        LOGGER.warning(
            "Gemini daily quota exhausted during hn_id=%s. Skipping further Gemini requests for the rest of this cycle.",
            hn_id,
        )


def should_refresh_comments(
    current_comment_count: int,
    last_summarized_comment_count: int,
    threshold: int,
    updates_done: int,
    max_updates: int,
) -> bool:
    if updates_done >= max_updates:
        return False
    return (current_comment_count - last_summarized_comment_count) >= threshold


def _usage_delta(before, after):
    return type(after)(
        prompt_token_count=after.prompt_token_count - before.prompt_token_count,
        candidates_token_count=after.candidates_token_count - before.candidates_token_count,
        cached_content_token_count=(
            after.cached_content_token_count - before.cached_content_token_count
        ),
        thoughts_token_count=after.thoughts_token_count - before.thoughts_token_count,
        total_token_count=after.total_token_count - before.total_token_count,
    )


def _format_usage_log(usage) -> str:
    return (
        f"prompt_tokens={usage.prompt_token_count}, "
        f"output_tokens={usage.candidates_token_count}, "
        f"cached_tokens={usage.cached_content_token_count}, "
        f"thought_tokens={usage.thoughts_token_count}, "
        f"total_tokens={usage.total_token_count}"
    )


def _format_cycle_stats(stats: CycleStats) -> str:
    return (
        f"frontpage_posts_seen={stats.frontpage_posts_seen}, "
        f"qualifying_posts={stats.qualifying_posts}, "
        f"processed_posts={stats.processed_posts}, "
        f"initial_publications={stats.initial_publications}, "
        f"comments_updates={stats.comments_updates}, "
        f"skipped_comment_updates={stats.skipped_comment_updates}, "
        f"failures={stats.failures}, "
        f"gemini_calls={stats.gemini_calls}"
    )
