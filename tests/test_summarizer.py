from hacker_news_summary_channel.summarizer import _is_daily_quota_exceeded


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
