import ssl
from urllib.error import HTTPError, URLError

from hacker_news_summary_channel.hn_client import _should_retry_http_error, _should_retry_url_error


def test_retryable_hn_http_errors() -> None:
    error = HTTPError(
        url="https://example.com", code=503, msg="Service Unavailable", hdrs=None, fp=None
    )
    assert _should_retry_http_error(error)


def test_retryable_hn_url_ssl_eof_error() -> None:
    error = URLError(ssl.SSLEOFError("unexpected eof"))
    assert _should_retry_url_error(error)


def test_retryable_hn_url_timeout_error() -> None:
    error = URLError(TimeoutError("timed out"))
    assert _should_retry_url_error(error)
