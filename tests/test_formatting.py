from hacker_news_summary_channel.formatting import (
    format_article_message,
    format_comments_message,
    sanitize_summary_text,
    telegram_text_length,
)
from hacker_news_summary_channel.models import FrontPagePost


def test_format_article_message() -> None:
    post = FrontPagePost(
        hn_id=123,
        rank=1,
        title="Example Post Title",
        url="https://example.com/post",
        domain="example.com",
        score=187,
        comment_count=64,
        text=None,
        post_type="story",
    )
    message = format_article_message(post, "<summary>", max_chars=4096)
    assert "Example Post Title" in message
    assert "example.com • 187 points • 64 comments" in message
    assert "https://news.ycombinator.com/item?id=123" in message
    assert "&lt;summary&gt;" in message


def test_format_comments_message() -> None:
    post = FrontPagePost(
        hn_id=123,
        rank=1,
        title="Example Post Title",
        url="https://example.com/post",
        domain="example.com",
        score=187,
        comment_count=64,
        text=None,
        post_type="story",
    )
    message = format_comments_message(post, "<summary>", max_chars=4096)
    assert "Comments Summary for: Example Post Title" in message
    assert "https://news.ycombinator.com/item?id=123" in message
    assert "&lt;summary&gt;" in message


def test_format_article_message_truncates_to_telegram_limit() -> None:
    post = FrontPagePost(
        hn_id=123,
        rank=1,
        title="Example Post Title",
        url="https://example.com/post",
        domain="example.com",
        score=187,
        comment_count=64,
        text=None,
        post_type="story",
    )
    message = format_article_message(post, "A" * 5000, max_chars=200)
    assert telegram_text_length(message) <= 200
    assert message.endswith("…")


def test_sanitize_summary_text_removes_markdown_and_bullets() -> None:
    raw = "**Main Themes**\n* First point\n* Second point\n\nAnother paragraph."
    cleaned = sanitize_summary_text(raw)
    assert "**" not in cleaned
    assert "* " not in cleaned
    assert "Main Themes" in cleaned
    assert "First point" in cleaned
    assert "Second point" in cleaned
