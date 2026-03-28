from __future__ import annotations

from html import escape, unescape

from .models import FrontPagePost


ARTICLE_FALLBACK_SUMMARY = "<could not generate article summary>"
COMMENTS_FALLBACK_SUMMARY = "<could not generate comments summary>"


def format_article_message(post: FrontPagePost, summary: str | None, max_chars: int) -> str:
    summary_text = summary.strip() if summary else ARTICLE_FALLBACK_SUMMARY
    url = f"https://news.ycombinator.com/item?id={post.hn_id}"
    source = post.domain or "news.ycombinator.com"
    header = (
        f"{escape(post.title)}\n"
        f"{escape(source)} • {post.score} points • {post.comment_count} comments\n"
        f"{url}\n\n"
    )
    available_summary_chars = _available_summary_chars(header, max_chars)
    return f"{header}{escape(_truncate_plain_text(summary_text, available_summary_chars))}"


def format_comments_message(post: FrontPagePost, summary: str | None, max_chars: int) -> str:
    summary_text = summary.strip() if summary else COMMENTS_FALLBACK_SUMMARY
    url = f"https://news.ycombinator.com/item?id={post.hn_id}"
    header = f"Comments Summary for: {escape(post.title)}\n{url}\n\n"
    available_summary_chars = _available_summary_chars(header, max_chars)
    return f"{header}{escape(_truncate_plain_text(summary_text, available_summary_chars))}"


def telegram_text_length(text: str) -> int:
    return len(unescape(text))


def _available_summary_chars(header: str, max_chars: int) -> int:
    available = max_chars - telegram_text_length(header)
    return max(1, available)


def _truncate_plain_text(text: str, max_chars: int) -> str:
    trimmed = text.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    if max_chars <= 1:
        return "…"
    return f"{trimmed[: max_chars - 1].rstrip()}…"
