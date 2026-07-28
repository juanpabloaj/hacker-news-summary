from __future__ import annotations

from pathlib import Path

import pytest

from hacker_news_summary_channel.config import Config
from hacker_news_summary_channel.models import (
    FetchResult,
    FrontPagePost,
    GeminiResponse,
    GeminiUsage,
)
from hacker_news_summary_channel.service import (
    ArticleSummaryResult,
    CommentsSummaryResult,
    PollingService,
)
from hacker_news_summary_channel.storage import Storage
from hacker_news_summary_channel.summarizer import GeminiNoTextResponseError


class FakeTelegramClient:
    def __init__(
        self,
        fail_on_send_number: int | None = None,
        fail_on_delete: bool = False,
    ) -> None:
        self.fail_on_send_number = fail_on_send_number
        self.fail_on_delete = fail_on_delete
        self.send_calls: list[str] = []
        self.edit_calls: list[tuple[int, str]] = []
        self.deleted_message_ids: list[int] = []

    def send_message(self, text: str) -> int:
        self.send_calls.append(text)
        if self.fail_on_send_number == len(self.send_calls):
            raise RuntimeError("telegram send failed")
        return 1000 + len(self.send_calls)

    def edit_message(self, message_id: int, text: str) -> None:
        self.edit_calls.append((message_id, text))

    def delete_message(self, message_id: int) -> None:
        if self.fail_on_delete:
            raise RuntimeError("telegram delete failed")
        self.deleted_message_ids.append(message_id)


class FakeGeminiClient:
    def __init__(
        self,
        *,
        article_error: Exception | None = None,
        url_error: Exception | None = None,
        url_response: GeminiResponse | None = None,
    ) -> None:
        self.article_error = article_error
        self.url_error = url_error
        self.url_response = url_response
        self.article_calls = 0
        self.url_calls = 0

    def summarize_article(self, *_args, **_kwargs) -> GeminiResponse:
        self.article_calls += 1
        if self.article_error:
            raise self.article_error
        raise AssertionError("No article response configured.")

    def summarize_article_from_url(self, *_args, **_kwargs) -> GeminiResponse:
        self.url_calls += 1
        if self.url_error:
            raise self.url_error
        if self.url_response:
            return self.url_response
        raise AssertionError("No URL-context response configured.")


def test_initial_publication_is_deferred_until_both_summaries_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    telegram_client = FakeTelegramClient()
    service = PollingService(
        Config(), storage, gemini_client=object(), telegram_client=telegram_client
    )
    monkeypatch.setattr(
        service,
        "_generate_article_summary",
        lambda _post: ArticleSummaryResult(
            summary="<could not generate article summary>",
            used_fallback=True,
            content_hash=None,
        ),
    )
    comments_calls = []

    def fail_if_comments_are_generated(_post):
        comments_calls.append(_post.hn_id)
        raise AssertionError("Comments should not be summarized after an article failure.")

    monkeypatch.setattr(service, "_generate_comments_summary", fail_if_comments_are_generated)

    published = service._publish_initial_messages(post)

    assert not published
    assert comments_calls == []
    assert telegram_client.send_calls == []
    assert storage.get_post(post.hn_id).article_message_id is None
    assert storage.get_post(post.hn_id).comments_message_id is None
    assert storage.get_latest_article_summary(post.hn_id) is None
    assert storage.get_latest_comment_summary(post.hn_id) is None


def test_article_no_text_response_uses_url_context_fallback_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    gemini_client = FakeGeminiClient(
        article_error=_no_text_error("SAFETY", terminal=True),
        url_response=GeminiResponse(
            text="URL context summary",
            usage=GeminiUsage(prompt_token_count=20, candidates_token_count=5),
            response_id="url-success",
        ),
    )
    service = PollingService(
        Config(), storage, gemini_client=gemini_client, telegram_client=FakeTelegramClient()
    )
    monkeypatch.setattr(
        "hacker_news_summary_channel.service.fetch_article_or_text",
        lambda **_kwargs: FetchResult(
            fetch_method="local_http_fetch",
            content="Article content",
            content_hash="content-1",
            source_url=post.url,
            raw_content="Article content",
            gemini_input_text="Article content",
        ),
    )

    result = service._generate_article_summary(post)

    assert result.summary == "URL context summary"
    assert not result.used_fallback
    assert gemini_client.article_calls == 1
    assert gemini_client.url_calls == 1
    assert storage.get_gemini_call_count() == 2
    assert storage.get_gemini_failure_count() == 1
    assert storage.get_post(post.hn_id).article_summary_terminal_reason is None


def test_repeated_terminal_no_text_response_marks_article_as_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    gemini_client = FakeGeminiClient(
        article_error=_no_text_error("SAFETY", terminal=True),
        url_error=_no_text_error("RECITATION", terminal=True),
    )
    service = PollingService(
        Config(), storage, gemini_client=gemini_client, telegram_client=FakeTelegramClient()
    )
    fetch_calls = []

    def fetch_article(**_kwargs):
        fetch_calls.append(post.hn_id)
        return FetchResult(
            fetch_method="local_http_fetch",
            content="Article content",
            content_hash="content-1",
            source_url=post.url,
        )

    monkeypatch.setattr(
        "hacker_news_summary_channel.service.fetch_article_or_text",
        fetch_article,
    )

    first_result = service._generate_article_summary(post)
    second_result = service._generate_article_summary(post)

    assert first_result.used_fallback
    assert second_result.used_fallback
    assert gemini_client.article_calls == 1
    assert gemini_client.url_calls == 1
    assert fetch_calls == [post.hn_id]
    assert storage.get_gemini_call_count() == 2
    assert storage.get_gemini_failure_count() == 2
    assert storage.get_post(post.hn_id).article_summary_terminal_reason == "RECITATION"


def test_initial_publication_stores_state_only_after_both_messages_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    telegram_client = FakeTelegramClient()
    service = PollingService(
        Config(), storage, gemini_client=object(), telegram_client=telegram_client
    )
    monkeypatch.setattr(
        service,
        "_generate_article_summary",
        lambda _post: ArticleSummaryResult(
            summary="Article summary",
            used_fallback=False,
            content_hash="content-1",
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_comments_summary",
        lambda _post: CommentsSummaryResult(
            summary="Comments summary",
            comment_tree_hash="tree-1",
            used_fallback=False,
        ),
    )

    published = service._publish_initial_messages(post)

    assert published
    assert len(telegram_client.send_calls) == 2
    record = storage.get_post(post.hn_id)
    assert record.article_message_id == 1001
    assert record.comments_message_id == 1002
    assert storage.get_latest_article_summary(post.hn_id)["summary_text"] == "Article summary"
    assert storage.get_latest_comment_summary(post.hn_id)["summary_text"] == "Comments summary"


def test_initial_publication_rolls_back_first_message_when_second_send_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    telegram_client = FakeTelegramClient(fail_on_send_number=2)
    service = PollingService(
        Config(), storage, gemini_client=object(), telegram_client=telegram_client
    )
    monkeypatch.setattr(
        service,
        "_generate_article_summary",
        lambda _post: ArticleSummaryResult(
            summary="Article summary",
            used_fallback=False,
            content_hash="content-1",
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_comments_summary",
        lambda _post: CommentsSummaryResult(
            summary="Comments summary",
            comment_tree_hash="tree-1",
            used_fallback=False,
        ),
    )

    with pytest.raises(RuntimeError, match="telegram send failed"):
        service._publish_initial_messages(post)

    record = storage.get_post(post.hn_id)
    assert record.article_message_id is None
    assert record.comments_message_id is None
    assert telegram_client.deleted_message_ids == [1001]
    assert storage.get_latest_article_summary(post.hn_id) is None
    assert storage.get_latest_comment_summary(post.hn_id) is None


def test_process_post_clears_partial_publication_before_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    storage.set_article_message_id(post.hn_id, 777)
    telegram_client = FakeTelegramClient()
    service = PollingService(
        Config(), storage, gemini_client=object(), telegram_client=telegram_client
    )
    monkeypatch.setattr(
        service,
        "_generate_article_summary",
        lambda _post: ArticleSummaryResult(
            summary="Article summary",
            used_fallback=False,
            content_hash="content-1",
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_comments_summary",
        lambda _post: CommentsSummaryResult(
            summary="Comments summary",
            comment_tree_hash="tree-1",
            used_fallback=False,
        ),
    )

    service._process_post(post, _empty_stats())

    record = storage.get_post(post.hn_id)
    assert telegram_client.deleted_message_ids == [777]
    assert len(telegram_client.send_calls) == 2
    assert record.article_message_id == 1001
    assert record.comments_message_id == 1002


def test_process_post_recovers_when_messages_exist_but_comment_summary_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    storage.set_article_message_id(post.hn_id, 700)
    storage.set_comments_message_id(post.hn_id, 701)
    telegram_client = FakeTelegramClient()
    service = PollingService(
        Config(), storage, gemini_client=object(), telegram_client=telegram_client
    )
    monkeypatch.setattr(
        service,
        "_generate_article_summary",
        lambda _post: ArticleSummaryResult(
            summary="Article summary",
            used_fallback=False,
            content_hash="content-1",
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_comments_summary",
        lambda _post: CommentsSummaryResult(
            summary="Comments summary",
            comment_tree_hash="tree-1",
            used_fallback=False,
        ),
    )

    service._process_post(post, _empty_stats())

    record = storage.get_post(post.hn_id)
    assert telegram_client.deleted_message_ids == [700, 701]
    assert len(telegram_client.send_calls) == 2
    assert record.article_message_id == 1001
    assert record.comments_message_id == 1002
    assert storage.get_latest_comment_summary(post.hn_id)["summary_text"] == "Comments summary"


def test_process_post_keeps_retryable_state_when_partial_cleanup_delete_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = Storage(str(tmp_path / "app.db"))
    storage.initialize()
    post = _sample_post()
    storage.upsert_post(post)
    storage.set_article_message_id(post.hn_id, 888)
    telegram_client = FakeTelegramClient(fail_on_delete=True)
    service = PollingService(
        Config(), storage, gemini_client=object(), telegram_client=telegram_client
    )
    monkeypatch.setattr(
        service,
        "_generate_article_summary",
        lambda _post: ArticleSummaryResult(
            summary="<could not generate article summary>",
            used_fallback=True,
            content_hash=None,
        ),
    )
    monkeypatch.setattr(
        service,
        "_generate_comments_summary",
        lambda _post: CommentsSummaryResult(
            summary="Comments summary",
            comment_tree_hash="tree-1",
            used_fallback=False,
        ),
    )

    service._process_post(post, _empty_stats())

    record = storage.get_post(post.hn_id)
    assert telegram_client.deleted_message_ids == []
    assert telegram_client.send_calls == []
    assert record.article_message_id is None
    assert record.comments_message_id is None
    assert storage.get_latest_article_summary(post.hn_id) is None
    assert storage.get_latest_comment_summary(post.hn_id) is None


def _sample_post() -> FrontPagePost:
    return FrontPagePost(
        hn_id=123456,
        rank=1,
        title="Example post",
        url="https://example.com/post",
        domain="example.com",
        score=150,
        comment_count=80,
        text=None,
        post_type="link",
    )


def _no_text_error(reason: str, *, terminal: bool) -> GeminiNoTextResponseError:
    return GeminiNoTextResponseError(
        reason,
        response_id=f"response-{reason.lower()}",
        usage=GeminiUsage(prompt_token_count=10, total_token_count=10),
        diagnostic_metadata='{"candidates":[]}',
        terminal=terminal,
    )


def _empty_stats():
    from hacker_news_summary_channel.service import CycleStats

    return CycleStats()
