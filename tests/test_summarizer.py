import json
from urllib.error import URLError

import pytest

from hacker_news_summary_channel.models import GeminiUsage
from hacker_news_summary_channel.summarizer import (
    GeminiDailyQuotaExceededError,
    GeminiNoTextResponseError,
    GeminiTransientError,
    _classify_http_error,
    _extract_response_text,
    _is_daily_quota_exceeded,
    _should_retry_http_error,
    _should_retry_url_error,
)


def test_detects_daily_quota_exceeded_error_payload() -> None:
    payload = {
        "error": {
            "code": 429,
            "details": [
                {
                    "violations": [
                        {
                            "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                        }
                    ]
                }
            ],
        }
    }
    assert _is_daily_quota_exceeded(payload)


def test_daily_quota_error_is_not_retryable() -> None:
    assert not _should_retry_http_error(429, GeminiDailyQuotaExceededError("quota exhausted"))


def test_timeout_url_error_is_retryable() -> None:
    assert _should_retry_url_error(URLError(TimeoutError("timed out")))


def test_503_is_classified_as_transient_error() -> None:
    error = _classify_http_error(503, '{"error":{"code":503}}')
    assert isinstance(error, GeminiTransientError)


def test_no_text_response_preserves_prompt_block_diagnostics() -> None:
    body = {
        "responseId": "response-1",
        "promptFeedback": {
            "blockReason": "PROHIBITED_CONTENT",
            "blockReasonMessage": "  Request was blocked.  ",
            "safetyRatings": [
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "probability": "HIGH",
                    "blocked": True,
                    "ignoredField": "not persisted",
                }
            ],
        },
        "usageMetadata": {"promptTokenCount": 42, "totalTokenCount": 42},
    }

    with pytest.raises(GeminiNoTextResponseError) as raised:
        _extract_response_text(body)

    error = raised.value
    assert error.reason == "PROHIBITED_CONTENT"
    assert error.response_id == "response-1"
    assert error.usage == GeminiUsage(prompt_token_count=42, total_token_count=42)
    assert error.terminal
    metadata = json.loads(error.diagnostic_metadata)
    assert metadata["prompt_feedback"] == {
        "block_reason": "PROHIBITED_CONTENT",
        "block_reason_message": "Request was blocked.",
        "safety_ratings": [
            {
                "blocked": True,
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "probability": "HIGH",
            }
        ],
    }


def test_no_text_response_uses_candidate_finish_reason() -> None:
    body = {
        "candidates": [
            {
                "index": 0,
                "finishReason": "RECITATION",
                "finishMessage": "Output matched source material.",
            }
        ]
    }

    with pytest.raises(GeminiNoTextResponseError) as raised:
        _extract_response_text(body)

    assert raised.value.reason == "RECITATION"
    assert raised.value.terminal


def test_unknown_no_text_response_remains_retryable_in_later_cycle() -> None:
    with pytest.raises(GeminiNoTextResponseError) as raised:
        _extract_response_text({"candidates": []})

    assert raised.value.reason == "NO_CANDIDATES"
    assert not raised.value.terminal
