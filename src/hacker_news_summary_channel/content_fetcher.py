from __future__ import annotations

import hashlib
import logging
import re
from html import unescape
from html.parser import HTMLParser
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import FetchResult

LOGGER = logging.getLogger(__name__)
DEFAULT_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (compatible; HackerNewsResumeChannel/0.1; +https://news.ycombinator.com/)"
)


class _VisibleTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        return normalize_text(" ".join(self._chunks))


def fetch_article_or_text(
    url: str | None,
    fallback_text: str | None,
    timeout_seconds: int,
    max_chars: int,
) -> FetchResult:
    if url:
        return fetch_article(url, timeout_seconds=timeout_seconds, max_chars=max_chars)
    if fallback_text:
        normalized = normalize_text(html_to_text(fallback_text))[:max_chars]
        return FetchResult(
            fetch_method="hn_post_text",
            content=normalized,
            content_hash=_hash_text(normalized),
            source_url=None,
            raw_content=fallback_text[:max_chars],
            gemini_input_text=normalized,
        )
    return FetchResult(
        fetch_method="missing_source",
        content=None,
        content_hash=None,
        source_url=url,
        error_message="No article URL or fallback text was available.",
    )


def fetch_article(url: str, timeout_seconds: int, max_chars: int) -> FetchResult:
    request = Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get_content_type()
            raw_body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as error:
        LOGGER.warning("Article request failed for %s with HTTP %s", url, error.code)
        return FetchResult(
            fetch_method="local_http_fetch",
            content=None,
            content_hash=None,
            source_url=url,
            error_message=f"HTTP error {error.code}",
        )
    except URLError as error:
        LOGGER.warning("Article request failed for %s with URL error %s", url, error.reason)
        return FetchResult(
            fetch_method="local_http_fetch",
            content=None,
            content_hash=None,
            source_url=url,
            error_message=f"URL error: {error.reason}",
        )
    except TimeoutError:
        LOGGER.warning("Article request timed out for %s", url)
        return FetchResult(
            fetch_method="local_http_fetch",
            content=None,
            content_hash=None,
            source_url=url,
            error_message="Timeout",
        )

    decoded = raw_body.decode(charset, errors="replace")
    text = normalize_text(html_to_text(decoded) if content_type == "text/html" else decoded)
    if not text:
        return FetchResult(
            fetch_method="local_http_fetch",
            content=None,
            content_hash=None,
            source_url=url,
            raw_content=decoded[:max_chars],
            error_message="No readable content extracted.",
        )
    truncated = text[:max_chars]
    return FetchResult(
        fetch_method="local_http_fetch",
        content=truncated,
        content_hash=_hash_text(truncated),
        source_url=url,
        raw_content=decoded[:max_chars],
        gemini_input_text=truncated,
    )


def html_to_text(html: str) -> str:
    extractor = _VisibleTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def normalize_text(text: str) -> str:
    stripped = re.sub(r"\s+", " ", unescape(text)).strip()
    return stripped


def get_domain(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.netloc or None


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
