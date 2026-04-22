from __future__ import annotations

from pathlib import Path

import pytest

from hacker_news_summary_channel.config import Config, _get_env_bool


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    # Run from a tmp directory so the project .env is not auto-discovered.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_EXPANDABLE_SUMMARIES", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "x")
    return monkeypatch


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_get_env_bool_parses_common_values(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("MY_FLAG", value)
    assert _get_env_bool("MY_FLAG", default=not expected) is expected


def test_get_env_bool_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_FLAG", raising=False)
    assert _get_env_bool("MY_FLAG", default=True) is True
    assert _get_env_bool("MY_FLAG", default=False) is False


def test_get_env_bool_defaults_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_FLAG", "   ")
    assert _get_env_bool("MY_FLAG", default=True) is True


def test_get_env_bool_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_FLAG", "maybe")
    with pytest.raises(ValueError, match="MY_FLAG"):
        _get_env_bool("MY_FLAG", default=True)


def test_config_telegram_expandable_summaries_default_true(
    clean_env: pytest.MonkeyPatch,
) -> None:
    config = Config.from_env()
    assert config.telegram_expandable_summaries is True


def test_config_telegram_expandable_summaries_can_be_disabled(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("TELEGRAM_EXPANDABLE_SUMMARIES", "false")
    config = Config.from_env()
    assert config.telegram_expandable_summaries is False
