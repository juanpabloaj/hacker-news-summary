from http.client import RemoteDisconnected
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from hacker_news_summary_channel import telegram
from hacker_news_summary_channel.telegram import TelegramClient, _retry_after_seconds


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _client() -> TelegramClient:
    return TelegramClient(
        bot_token="token",
        channel_id="@channel",
        parse_mode="HTML",
        timeout_seconds=5,
        max_message_chars=4096,
    )


def _http_error(code: int, body: str = "{}") -> HTTPError:
    return HTTPError("https://api.telegram.org", code, "err", {}, io.BytesIO(body.encode()))


def _ok_response(message_id: int = 7) -> _FakeResponse:
    return _FakeResponse(json.dumps({"ok": True, "result": {"message_id": message_id}}).encode())


def test_send_message_retries_transient_http_error(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _http_error(502, '{"ok":false,"error_code":502}')
        return _ok_response()

    monkeypatch.setattr(telegram, "urlopen", fake_urlopen)
    monkeypatch.setattr(telegram.time, "sleep", lambda _seconds: None)

    assert _client().send_message("hola") == 7
    assert calls == 3


def test_edit_message_retries_remote_disconnected(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        if calls < 2:
            raise RemoteDisconnected("Remote end closed connection without response")
        return _FakeResponse(json.dumps({"ok": True, "result": True}).encode())

    monkeypatch.setattr(telegram, "urlopen", fake_urlopen)
    monkeypatch.setattr(telegram.time, "sleep", lambda _seconds: None)

    _client().edit_message(1, "hola")
    assert calls == 2


def test_non_retryable_http_error_fails_immediately(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        raise _http_error(400, '{"ok":false,"error_code":400}')

    monkeypatch.setattr(telegram, "urlopen", fake_urlopen)
    monkeypatch.setattr(telegram.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError):
        _client().send_message("hola")
    assert calls == 1


def test_persistent_transient_error_raises_after_max_retries(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        raise URLError("boom")

    monkeypatch.setattr(telegram, "urlopen", fake_urlopen)
    monkeypatch.setattr(telegram.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError):
        _client().send_message("hola")
    assert calls == telegram.POST_MAX_RETRIES


def test_retry_after_seconds_parsing() -> None:
    assert _retry_after_seconds('{"parameters":{"retry_after":5}}') == 5.0
    assert _retry_after_seconds('{"parameters":{"retry_after":900}}') == telegram.MAX_RETRY_AFTER_SECONDS
    assert _retry_after_seconds("not json") is None
    assert _retry_after_seconds('{"ok":false}') is None
