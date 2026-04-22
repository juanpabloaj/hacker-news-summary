from __future__ import annotations

from html import escape, unescape
import re

from .models import FrontPagePost


ARTICLE_FALLBACK_SUMMARY = "<could not generate article summary>"
COMMENTS_FALLBACK_SUMMARY = "<could not generate comments summary>"

_EXPANDABLE_OPEN = "<blockquote expandable>"
_EXPANDABLE_CLOSE = "</blockquote>"
_EXPANDABLE_OVERHEAD = len(_EXPANDABLE_OPEN) + len(_EXPANDABLE_CLOSE)


def format_article_message(
    post: FrontPagePost,
    summary: str | None,
    max_chars: int,
    expandable: bool = True,
) -> str:
    summary_text = sanitize_summary_text(summary) if summary else ARTICLE_FALLBACK_SUMMARY
    url = f"https://news.ycombinator.com/item?id={post.hn_id}"
    source = post.domain or "news.ycombinator.com"
    header = (
        f"{escape(post.title)}\n"
        f"{escape(source)} • {post.score} points • {post.comment_count} comments\n"
        f"{url}\n\n"
    )
    available_summary_chars = _available_summary_chars(header, max_chars, expandable)
    body = escape(_truncate_plain_text(summary_text, available_summary_chars))
    return f"{header}{_wrap_summary(body, expandable)}"


def format_comments_message(
    post: FrontPagePost,
    summary: str | None,
    max_chars: int,
    expandable: bool = True,
) -> str:
    summary_text = sanitize_summary_text(summary) if summary else COMMENTS_FALLBACK_SUMMARY
    url = f"https://news.ycombinator.com/item?id={post.hn_id}"
    header = f"Comments Summary for: {escape(post.title)}\n{url}\n\n"
    available_summary_chars = _available_summary_chars(header, max_chars, expandable)
    body = escape(_truncate_plain_text(summary_text, available_summary_chars))
    return f"{header}{_wrap_summary(body, expandable)}"


def _wrap_summary(body: str, expandable: bool) -> str:
    if not expandable:
        return body
    return f"{_EXPANDABLE_OPEN}{body}{_EXPANDABLE_CLOSE}"


def sanitize_summary_text(summary: str | None) -> str:
    if not summary:
        return ""
    text = summary.strip()
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"(?m)^\s*[*-]\s+", "", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", "", text)
    text = re.sub(r"(?m)^([A-Z][A-Za-z0-9 /&()-]{2,60}):\s*$", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def telegram_text_length(text: str) -> int:
    return len(unescape(text))


def _available_summary_chars(header: str, max_chars: int, expandable: bool = False) -> int:
    overhead = _EXPANDABLE_OVERHEAD if expandable else 0
    available = max_chars - telegram_text_length(header) - overhead
    return max(1, available)


def _truncate_plain_text(text: str, max_chars: int) -> str:
    trimmed = text.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    if max_chars <= 1:
        return "…"
    return f"{trimmed[: max_chars - 1].rstrip()}…"
