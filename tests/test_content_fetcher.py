from http.client import RemoteDisconnected

from hacker_news_summary_channel import content_fetcher
from hacker_news_summary_channel.content_fetcher import fetch_article


def test_fetch_article_handles_remote_disconnected(monkeypatch) -> None:
    def fake_urlopen(_request, timeout):
        raise RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(content_fetcher, "urlopen", fake_urlopen)

    result = fetch_article("https://example.com/article", timeout_seconds=1, max_chars=1000)

    assert result.fetch_method == "local_http_fetch"
    assert result.content is None
    assert result.content_hash is None
    assert result.source_url == "https://example.com/article"
    assert "Remote disconnected" in result.error_message
