from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import GeminiResponse, GeminiUsage

LOGGER = logging.getLogger(__name__)


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout_seconds: int) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def summarize_article(
        self, title: str, source_url: str | None, content: str, max_chars: int
    ) -> GeminiResponse:
        prompt = (
            "Write a concise English summary of the linked content. "
            "Focus on the main claims, findings, and relevant context. "
            "Keep it factual and compact. "
            f"Return plain text only. Maximum length: {max_chars} characters.\n\n"
            f"Title: {title}\n"
            f"Source URL: {source_url or 'N/A'}\n"
            f"Content:\n{content}"
        )
        response = self._generate_text(prompt)
        response.text = _truncate_text(response.text, max_chars)
        return response

    def summarize_article_from_url(
        self, title: str, source_url: str, max_chars: int
    ) -> GeminiResponse:
        prompt = (
            "Write a concise English summary of the linked content. "
            "Focus on the main claims, findings, and relevant context. "
            "Keep it factual and compact. "
            f"Return plain text only. Maximum length: {max_chars} characters.\n\n"
            f"Title: {title}\n"
            f"URL: {source_url}\n"
            "Use the provided URL as context."
        )
        response = self._generate_text(prompt, use_url_context=True)
        response.text = _truncate_text(response.text, max_chars)
        return response

    def summarize_comments(self, title: str, comments_text: str, max_chars: int) -> GeminiResponse:
        prompt = (
            "Write a concise English summary of a Hacker News discussion. "
            "Highlight the main themes, disagreements, corrections, and expert insights. "
            "Keep it factual and compact. "
            f"Return plain text only. Maximum length: {max_chars} characters.\n\n"
            f"Post title: {title}\n"
            f"Comments:\n{comments_text}"
        )
        response = self._generate_text(prompt)
        response.text = _truncate_text(response.text, max_chars)
        return response

    def _generate_text(self, prompt: str, use_url_context: bool = False) -> GeminiResponse:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": prompt,
                        }
                    ]
                }
            ]
        }
        if use_url_context:
            payload["tools"] = [{"url_context": {}}]
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            LOGGER.warning("Gemini request failed with HTTP %s: %s", error.code, detail)
            raise RuntimeError(f"Gemini request failed with HTTP {error.code}") from error
        except URLError as error:
            LOGGER.warning("Gemini request failed with URL error: %s", error.reason)
            raise RuntimeError(f"Gemini request failed: {error.reason}") from error

        return GeminiResponse(
            text=_extract_response_text(body),
            usage=_extract_usage(body.get("usageMetadata") or {}),
            response_id=body.get("responseId"),
        )


def _extract_response_text(body: dict) -> str:
    candidates = body.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        texts = [part.get("text", "") for part in parts if part.get("text")]
        if texts:
            return "\n".join(texts).strip()
    raise RuntimeError("Gemini response did not include text output.")


def _truncate_text(text: str, max_chars: int) -> str:
    trimmed = text.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    if max_chars <= 1:
        return "…"
    return f"{trimmed[: max_chars - 1].rstrip()}…"


def _extract_usage(usage_metadata: dict) -> GeminiUsage:
    return GeminiUsage(
        prompt_token_count=int(usage_metadata.get("promptTokenCount", 0) or 0),
        candidates_token_count=int(usage_metadata.get("candidatesTokenCount", 0) or 0),
        cached_content_token_count=int(usage_metadata.get("cachedContentTokenCount", 0) or 0),
        thoughts_token_count=int(usage_metadata.get("thoughtsTokenCount", 0) or 0),
        total_token_count=int(usage_metadata.get("totalTokenCount", 0) or 0),
    )
