from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class FrontPagePost:
    hn_id: int
    rank: int
    title: str
    url: str | None
    domain: str | None
    score: int
    comment_count: int
    text: str | None
    post_type: str


@dataclass(slots=True)
class FetchResult:
    fetch_method: str
    content: str | None
    content_hash: str | None
    source_url: str | None
    raw_content: str | None = None
    gemini_input_text: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class GeminiUsage:
    prompt_token_count: int = 0
    candidates_token_count: int = 0
    cached_content_token_count: int = 0
    thoughts_token_count: int = 0
    total_token_count: int = 0


@dataclass(slots=True)
class GeminiResponse:
    text: str
    usage: GeminiUsage
    response_id: str | None = None


@dataclass(slots=True)
class PostRecord:
    hn_id: int
    title: str
    url: str | None
    domain: str | None
    current_score: int
    current_comment_count: int
    current_frontpage_rank: int
    article_message_id: int | None
    comments_message_id: int | None
    comment_update_count: int
