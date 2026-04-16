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
from .summarizer import (
    GeminiClient,
    GeminiDailyQuotaExceededError,
    GeminiError,
    GeminiTransientError,
)
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


@dataclass(slots=True)
class ArticleSummaryResult:
    summary: str
    used_fallback: bool
    content_hash: str | None


@dataclass(slots=True)
class CommentsSummaryResult:
    summary: str
    comment_tree_hash: str
    used_fallback: bool


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
        self.gemini_temporarily_unavailable = False
        self.gemini_consecutive_transient_failures = 0

    def run_cycle(self) -> None:
        self.storage.initialize()
        self.gemini_daily_quota_exhausted = False
        self.gemini_temporarily_unavailable = False
        self.gemini_consecutive_transient_failures = 0
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
        if self._has_partial_publication(record):
            self._clear_partial_publication(post, record)
            record = self.storage.get_post(post.hn_id) or record
        if record.article_message_id is None or record.comments_message_id is None:
            if self._publish_initial_messages(post):
                stats.initial_publications += 1
            return
        latest_comment_summary = self.storage.get_latest_comment_summary(post.hn_id)
        if latest_comment_summary is None:
            self._clear_partial_publication(post, record)
            if self._publish_initial_messages(post):
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

    def _publish_initial_messages(self, post: FrontPagePost) -> bool:
        article_result = self._generate_article_summary(post)
        comments_result = self._generate_comments_summary(post)
        if article_result.used_fallback or comments_result.used_fallback:
            LOGGER.info(
                "Deferring Telegram publication for hn_id=%s until both summaries are available.",
                post.hn_id,
            )
            self.storage.clear_publication_state(post.hn_id)
            return False

        article_message_id: int | None = None
        try:
            article_message_id = self.telegram_client.send_message(
                format_article_message(
                    post,
                    article_result.summary,
                    max_chars=self.config.telegram_max_message_chars,
                )
            )
            comments_message_id = self.telegram_client.send_message(
                format_comments_message(
                    post,
                    comments_result.summary,
                    max_chars=self.config.telegram_max_message_chars,
                )
            )
        except Exception:
            if article_message_id is not None:
                self._delete_message_best_effort(post.hn_id, article_message_id)
            self.storage.clear_publication_state(post.hn_id)
            raise

        self.storage.set_article_message_id(post.hn_id, article_message_id)
        self.storage.set_comments_message_id(post.hn_id, comments_message_id)
        self.storage.store_article_summary(
            post.hn_id,
            article_result.content_hash,
            self.config.gemini_model,
            article_result.summary,
        )
        self.storage.store_comment_summary(
            post.hn_id,
            comment_tree_hash=comments_result.comment_tree_hash,
            comment_count=post.comment_count,
            model_name=self.config.gemini_model,
            summary_text=comments_result.summary,
        )
        LOGGER.info("Published article and comments messages for hn_id=%s", post.hn_id)
        return True

    def _refresh_comments_message(self, post: FrontPagePost, record: PostRecord) -> bool:
        comments_result = self._generate_comments_summary(post)
        if comments_result.used_fallback:
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
                comments_result.summary,
                max_chars=self.config.telegram_max_message_chars,
            ),
        )
        self.storage.store_comment_summary(
            post.hn_id,
            comment_tree_hash=comments_result.comment_tree_hash,
            comment_count=post.comment_count,
            model_name=self.config.gemini_model,
            summary_text=comments_result.summary,
        )
        self.storage.increment_comment_update_count(post.hn_id)
        LOGGER.info("Updated comments message for hn_id=%s", post.hn_id)
        return True

    def _generate_article_summary(self, post: FrontPagePost) -> ArticleSummaryResult:
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
            return ArticleSummaryResult(
                summary=str(latest["summary_text"]),
                used_fallback=False,
                content_hash=fetch_result.content_hash,
            )
        if not fetch_result.content:
            return self._generate_article_summary_from_url_fallback(
                post, local_fetch_error=fetch_result.error_message
            )
        if self._should_skip_gemini_requests():
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=fetch_result.content_hash,
            )
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
            self._mark_gemini_success()
            return ArticleSummaryResult(
                summary=response.text,
                used_fallback=False,
                content_hash=fetch_result.content_hash,
            )
        except GeminiDailyQuotaExceededError:
            self._mark_gemini_daily_quota_exhausted(post.hn_id)
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=fetch_result.content_hash,
            )
        except GeminiTransientError as error:
            self._mark_gemini_transient_failure(post.hn_id, error)
            LOGGER.warning("Article summary generation failed for hn_id=%s: %s", post.hn_id, error)
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=fetch_result.content_hash,
            )
        except GeminiError as error:
            LOGGER.warning("Article summary generation failed for hn_id=%s: %s", post.hn_id, error)
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=fetch_result.content_hash,
            )
        except Exception:
            LOGGER.exception("Unexpected article summary failure for hn_id=%s", post.hn_id)
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=fetch_result.content_hash,
            )

    def _generate_article_summary_from_url_fallback(
        self, post: FrontPagePost, local_fetch_error: str | None
    ) -> ArticleSummaryResult:
        if not post.url:
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=None,
            )
        self.storage.store_article_fetch(
            post.hn_id,
            fetch_method="gemini_url_context",
            source_url=post.url,
            raw_content=None,
            gemini_input_text=f"URL_CONTEXT: {post.url}",
            content_hash=None,
            error_message=local_fetch_error,
        )
        if self._should_skip_gemini_requests():
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=None,
            )
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
            self._mark_gemini_success()
            return ArticleSummaryResult(
                summary=response.text,
                used_fallback=False,
                content_hash=None,
            )
        except GeminiDailyQuotaExceededError:
            self._mark_gemini_daily_quota_exhausted(post.hn_id)
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=None,
            )
        except GeminiTransientError as error:
            self._mark_gemini_transient_failure(post.hn_id, error)
            LOGGER.warning(
                "Gemini URL-context article summary failed for hn_id=%s: %s",
                post.hn_id,
                error,
            )
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=None,
            )
        except GeminiError as error:
            LOGGER.warning(
                "Gemini URL-context article summary failed for hn_id=%s: %s",
                post.hn_id,
                error,
            )
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=None,
            )
        except Exception:
            LOGGER.exception(
                "Unexpected URL-context article summary failure for hn_id=%s", post.hn_id
            )
            return ArticleSummaryResult(
                summary=ARTICLE_FALLBACK_SUMMARY,
                used_fallback=True,
                content_hash=None,
            )

    def _generate_comments_summary(self, post: FrontPagePost) -> CommentsSummaryResult:
        comments_text, tree_hash = fetch_comments_text(
            item_id=post.hn_id,
            timeout_seconds=self.config.request_timeout_seconds,
            max_chars=self.config.comments_max_chars,
        )
        latest = self.storage.get_latest_comment_summary(post.hn_id)
        if comments_text and latest and latest["comment_tree_hash"] == tree_hash:
            LOGGER.info("Reusing cached comments summary for hn_id=%s", post.hn_id)
            return CommentsSummaryResult(
                summary=str(latest["summary_text"]),
                comment_tree_hash=tree_hash,
                used_fallback=False,
            )
        if not comments_text:
            return CommentsSummaryResult(
                summary=COMMENTS_FALLBACK_SUMMARY,
                comment_tree_hash=tree_hash,
                used_fallback=True,
            )
        if self._should_skip_gemini_requests():
            return CommentsSummaryResult(
                summary=COMMENTS_FALLBACK_SUMMARY,
                comment_tree_hash=tree_hash,
                used_fallback=True,
            )
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
            self._mark_gemini_success()
            return CommentsSummaryResult(
                summary=response.text,
                comment_tree_hash=tree_hash,
                used_fallback=False,
            )
        except GeminiDailyQuotaExceededError:
            self._mark_gemini_daily_quota_exhausted(post.hn_id)
            return CommentsSummaryResult(
                summary=COMMENTS_FALLBACK_SUMMARY,
                comment_tree_hash=tree_hash,
                used_fallback=True,
            )
        except GeminiTransientError as error:
            self._mark_gemini_transient_failure(post.hn_id, error)
            LOGGER.warning("Comments summary generation failed for hn_id=%s: %s", post.hn_id, error)
            return CommentsSummaryResult(
                summary=COMMENTS_FALLBACK_SUMMARY,
                comment_tree_hash=tree_hash,
                used_fallback=True,
            )
        except GeminiError as error:
            LOGGER.warning("Comments summary generation failed for hn_id=%s: %s", post.hn_id, error)
            return CommentsSummaryResult(
                summary=COMMENTS_FALLBACK_SUMMARY,
                comment_tree_hash=tree_hash,
                used_fallback=True,
            )
        except Exception:
            LOGGER.exception("Unexpected comments summary failure for hn_id=%s", post.hn_id)
            return CommentsSummaryResult(
                summary=COMMENTS_FALLBACK_SUMMARY,
                comment_tree_hash=tree_hash,
                used_fallback=True,
            )

    def _clear_partial_publication(self, post: FrontPagePost, record: PostRecord) -> None:
        if record.article_message_id is not None:
            self._delete_message_best_effort(post.hn_id, record.article_message_id)
        if record.comments_message_id is not None:
            self._delete_message_best_effort(post.hn_id, record.comments_message_id)
        self.storage.clear_publication_state(post.hn_id)
        LOGGER.warning("Cleared partial Telegram publication state for hn_id=%s", post.hn_id)

    def _delete_message_best_effort(self, hn_id: int, message_id: int) -> None:
        try:
            self.telegram_client.delete_message(message_id)
        except Exception as error:
            LOGGER.warning(
                "Failed to delete Telegram message_id=%s for hn_id=%s: %s",
                message_id,
                hn_id,
                error,
            )

    def _has_partial_publication(self, record: PostRecord) -> bool:
        return (record.article_message_id is None) != (record.comments_message_id is None)

    def _mark_gemini_daily_quota_exhausted(self, hn_id: int) -> None:
        if self.gemini_daily_quota_exhausted:
            return
        self.gemini_daily_quota_exhausted = True
        LOGGER.warning(
            "Gemini daily quota exhausted during hn_id=%s. Skipping further Gemini requests for the rest of this cycle.",
            hn_id,
        )

    def _mark_gemini_success(self) -> None:
        self.gemini_consecutive_transient_failures = 0

    def _mark_gemini_transient_failure(self, hn_id: int, error: GeminiTransientError) -> None:
        self.gemini_consecutive_transient_failures += 1
        if self.gemini_temporarily_unavailable:
            return
        if (
            self.gemini_consecutive_transient_failures
            < self.config.gemini_transient_failure_limit_per_cycle
        ):
            LOGGER.info(
                "Gemini transient failure count is now %s/%s for this cycle (hn_id=%s, error=%s).",
                self.gemini_consecutive_transient_failures,
                self.config.gemini_transient_failure_limit_per_cycle,
                hn_id,
                error,
            )
            return
        self.gemini_temporarily_unavailable = True
        LOGGER.warning(
            "Gemini transient failure limit reached during hn_id=%s. Skipping further Gemini requests for the rest of this cycle.",
            hn_id,
        )

    def _should_skip_gemini_requests(self) -> bool:
        return self.gemini_daily_quota_exhausted or self.gemini_temporarily_unavailable


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
