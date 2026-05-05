from http.client import RemoteDisconnected
import ssl
from urllib.error import HTTPError, URLError

from hacker_news_summary_channel import hn_client
from hacker_news_summary_channel.hn_client import (
    fetch_front_page_posts,
    fetch_item,
    _fetch_text,
    _should_retry_http_error,
    _should_retry_url_error,
)


class _FakeHeaders:
    def get_content_charset(self) -> str:
        return "utf-8"


class _FakeResponse:
    headers = _FakeHeaders()

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


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


def test_fetch_text_retries_remote_disconnected(
    monkeypatch,
) -> None:
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RemoteDisconnected("Remote end closed connection without response")
        return _FakeResponse(b"ok")

    monkeypatch.setattr(hn_client, "urlopen", fake_urlopen)
    monkeypatch.setattr(hn_client.time, "sleep", lambda _seconds: None)

    assert _fetch_text("https://example.com", timeout_seconds=1) == "ok"
    assert calls == 3


def test_fetch_item_returns_none_when_remote_disconnected_persists(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        raise RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(hn_client, "urlopen", fake_urlopen)
    monkeypatch.setattr(hn_client.time, "sleep", lambda _seconds: None)

    assert fetch_item(123, timeout_seconds=1) is None
    assert calls == hn_client.FETCH_MAX_RETRIES


def test_fetch_front_page_posts_returns_empty_when_remote_disconnected_persists(
    monkeypatch,
) -> None:
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        raise RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(hn_client, "urlopen", fake_urlopen)
    monkeypatch.setattr(hn_client.time, "sleep", lambda _seconds: None)

    assert fetch_front_page_posts(timeout_seconds=1) == []
    assert calls == hn_client.FETCH_MAX_RETRIES
