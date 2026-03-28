from __future__ import annotations

import json
import hashlib
import logging
import re
from html import unescape
from typing import Any
from urllib.request import Request, urlopen

from .content_fetcher import get_domain, normalize_text
from .models import FrontPagePost

LOGGER = logging.getLogger(__name__)

HN_FRONT_PAGE_URL = "https://news.ycombinator.com/"
HN_ITEM_API_URL = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
USER_AGENT = "Mozilla/5.0 (compatible; HackerNewsResumeChannel/0.1)"


def fetch_front_page_posts(timeout_seconds: int) -> list[FrontPagePost]:
    html = _fetch_text(HN_FRONT_PAGE_URL, timeout_seconds)
    entries = _parse_front_page_entries(html)
    posts: list[FrontPagePost] = []
    for entry in entries:
        item = fetch_item(entry["hn_id"], timeout_seconds)
        if not item or item.get("deleted") or item.get("dead"):
            continue
        url = item.get("url")
        text = normalize_text(_html_to_plain(item.get("text", ""))) if item.get("text") else None
        posts.append(
            FrontPagePost(
                hn_id=entry["hn_id"],
                rank=entry["rank"],
                title=item.get("title", "Untitled"),
                url=url,
                domain=get_domain(url),
                score=int(item.get("score", 0)),
                comment_count=int(item.get("descendants", 0)),
                text=text,
                post_type=item.get("type", "story"),
            )
        )
    return posts


def fetch_item(item_id: int, timeout_seconds: int) -> dict[str, Any] | None:
    url = HN_ITEM_API_URL.format(item_id=item_id)
    raw_json = _fetch_text(url, timeout_seconds)
    return json.loads(raw_json)


def fetch_comments_text(item_id: int, timeout_seconds: int, max_chars: int) -> tuple[str, str]:
    item = fetch_item(item_id, timeout_seconds)
    if not item:
        return "", "empty"
    comments: list[str] = []
    for child_id in item.get("kids", []):
        _collect_comment_text(child_id, timeout_seconds, comments)
    joined = "\n".join(comments)
    normalized = normalize_text(joined)[:max_chars]
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    tree_signature = f"{item_id}:{len(comments)}:{digest}"
    return normalized, tree_signature


def _collect_comment_text(item_id: int, timeout_seconds: int, comments: list[str]) -> None:
    item = fetch_item(item_id, timeout_seconds)
    if not item or item.get("deleted") or item.get("dead"):
        return
    text = item.get("text")
    if text:
        plain_text = normalize_text(_html_to_plain(text))
        if plain_text:
            comments.append(plain_text)
    for child_id in item.get("kids", []):
        _collect_comment_text(child_id, timeout_seconds, comments)


def _fetch_text(url: str, timeout_seconds: int) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout_seconds) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _parse_front_page_entries(html: str) -> list[dict[str, int]]:
    pattern = re.compile(
        r'<tr class="athing(?: submission)?" id="(?P<id>\d+)">.*?<span class="rank">(?P<rank>\d+)\.</span>',
        re.S,
    )
    entries = []
    for match in pattern.finditer(html):
        entries.append({"hn_id": int(match.group("id")), "rank": int(match.group("rank"))})
    LOGGER.info("Fetched %s front page items.", len(entries))
    return entries


def _html_to_plain(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", unescape(html))
