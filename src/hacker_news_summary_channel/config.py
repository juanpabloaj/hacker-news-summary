from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path


def _get_env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    return int(raw_value)


_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _get_env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    normalized = raw_value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        f"Invalid boolean value for {name}: {raw_value!r}. "
        f"Expected one of {sorted(_TRUE_VALUES | _FALSE_VALUES)}."
    )


@dataclass(slots=True)
class Config:
    poll_interval_minutes: int = 60
    hn_min_points: int = 100
    comment_resummary_threshold: int = 50
    max_comment_updates_per_post: int = 3
    gemini_model: str = "gemini-2.5-flash-lite"
    db_path: str = "data/app.db"
    log_level: str = "INFO"
    telegram_parse_mode: str = "HTML"
    telegram_max_message_chars: int = 4096
    telegram_expandable_summaries: bool = True
    request_timeout_seconds: int = 20
    gemini_timeout_seconds: int = 60
    gemini_max_retries: int = 4
    gemini_retry_delay_seconds: int = 4
    gemini_transient_failure_limit_per_cycle: int = 3
    article_max_chars: int = 20000
    comments_max_chars: int = 24000
    article_summary_max_chars: int = 1400
    comments_summary_max_chars: int = 2200
    gemini_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_channel_id: str | None = None

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        return cls(
            poll_interval_minutes=_get_env_int("POLL_INTERVAL_MINUTES", 60),
            hn_min_points=_get_env_int("HN_MIN_POINTS", 100),
            comment_resummary_threshold=_get_env_int("COMMENT_RESUMMARY_THRESHOLD", 50),
            max_comment_updates_per_post=_get_env_int("MAX_COMMENT_UPDATES_PER_POST", 3),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            db_path=os.getenv("DB_PATH", "data/app.db"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            telegram_parse_mode=os.getenv("TELEGRAM_PARSE_MODE", "HTML"),
            telegram_max_message_chars=_get_env_int("TELEGRAM_MAX_MESSAGE_CHARS", 4096),
            telegram_expandable_summaries=_get_env_bool("TELEGRAM_EXPANDABLE_SUMMARIES", True),
            request_timeout_seconds=_get_env_int("REQUEST_TIMEOUT_SECONDS", 20),
            gemini_timeout_seconds=_get_env_int("GEMINI_TIMEOUT_SECONDS", 60),
            gemini_max_retries=_get_env_int("GEMINI_MAX_RETRIES", 4),
            gemini_retry_delay_seconds=_get_env_int("GEMINI_RETRY_DELAY_SECONDS", 4),
            gemini_transient_failure_limit_per_cycle=_get_env_int(
                "GEMINI_TRANSIENT_FAILURE_LIMIT_PER_CYCLE", 3
            ),
            article_max_chars=_get_env_int("ARTICLE_MAX_CHARS", 20000),
            comments_max_chars=_get_env_int("COMMENTS_MAX_CHARS", 24000),
            article_summary_max_chars=_get_env_int("ARTICLE_SUMMARY_MAX_CHARS", 1400),
            comments_summary_max_chars=_get_env_int("COMMENTS_SUMMARY_MAX_CHARS", 2200),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_channel_id=os.getenv("TELEGRAM_CHANNEL_ID"),
        )

    def configure_logging(self) -> None:
        logging.basicConfig(
            level=getattr(logging, self.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

    def log_effective_configuration(self, logger: logging.Logger, version: str) -> None:
        logger.info("Configuration:")
        logger.info("  version: %s", version)
        logger.info("  poll_interval_minutes: %s", self.poll_interval_minutes)
        logger.info("  hn_min_points: %s", self.hn_min_points)
        logger.info("  comment_resummary_threshold: %s", self.comment_resummary_threshold)
        logger.info("  max_comment_updates_per_post: %s", self.max_comment_updates_per_post)
        logger.info("  gemini_model: %s", self.gemini_model)
        logger.info("  db_path: %s", self.db_path)
        logger.info("  log_level: %s", self.log_level)
        logger.info("  telegram_parse_mode: %s", self.telegram_parse_mode)
        logger.info("  telegram_max_message_chars: %s", self.telegram_max_message_chars)
        logger.info("  telegram_expandable_summaries: %s", self.telegram_expandable_summaries)
        logger.info("  request_timeout_seconds: %s", self.request_timeout_seconds)
        logger.info("  gemini_timeout_seconds: %s", self.gemini_timeout_seconds)
        logger.info("  gemini_max_retries: %s", self.gemini_max_retries)
        logger.info("  gemini_retry_delay_seconds: %s", self.gemini_retry_delay_seconds)
        logger.info(
            "  gemini_transient_failure_limit_per_cycle: %s",
            self.gemini_transient_failure_limit_per_cycle,
        )
        logger.info("  article_max_chars: %s", self.article_max_chars)
        logger.info("  comments_max_chars: %s", self.comments_max_chars)
        logger.info("  article_summary_max_chars: %s", self.article_summary_max_chars)
        logger.info("  comments_summary_max_chars: %s", self.comments_summary_max_chars)
        logger.info("  gemini_api_key: %s", _secret_status(self.gemini_api_key))
        logger.info("  telegram_bot_token: %s", _secret_status(self.telegram_bot_token))
        logger.info("  telegram_channel_id: %s", _secret_status(self.telegram_channel_id))

    def validate(self) -> None:
        missing = []
        if not self.gemini_api_key:
            missing.append("GEMINI_API_KEY")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.telegram_channel_id:
            missing.append("TELEGRAM_CHANNEL_ID")
        if missing:
            missing_list = ", ".join(missing)
            raise ValueError(f"Missing required environment variables: {missing_list}")


def _secret_status(value: str | None) -> str:
    return "configured" if value else "missing"


def _load_dotenv(filename: str = ".env") -> None:
    dotenv_path = _find_dotenv(filename)
    if dotenv_path is None:
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def _find_dotenv(filename: str) -> Path | None:
    current = Path.cwd().resolve()
    for directory in (current, *current.parents):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None
