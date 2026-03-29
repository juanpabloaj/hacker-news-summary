from urllib.error import URLError

from hacker_news_summary_channel.summarizer import (
    GeminiDailyQuotaExceededError,
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
