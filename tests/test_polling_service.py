from __future__ import annotations

from pathlib import Path

import pytest

from hacker_news_summary_channel.config import Config
from hacker_news_summary_channel.models import FrontPagePost
from hacker_news_summary_channel.service import (
    ArticleSummaryResult,
    CommentsSummaryResult,
    PollingService,
)
from hacker_news_summary_channel.storage import Storage


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

    assert not published
    assert telegram_client.send_calls == []
    assert storage.get_post(post.hn_id).article_message_id is None
    assert storage.get_post(post.hn_id).comments_message_id is None
    assert storage.get_latest_article_summary(post.hn_id) is None
    assert storage.get_latest_comment_summary(post.hn_id) is None


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


def _empty_stats():
    from hacker_news_summary_channel.service import CycleStats

    return CycleStats()
