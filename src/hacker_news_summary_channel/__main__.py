from __future__ import annotations

import logging
import sys

from . import get_version
from .config import Config
from .service import PollingService
from .storage import Storage
from .summarizer import GeminiClient
from .telegram import TelegramClient


def main() -> int:
    config = Config.from_env()
    config.configure_logging()
    logger = logging.getLogger(__name__)
    current_version = get_version()
    logger.info("Starting hacker_news_summary_channel %s", current_version)
    config.log_effective_configuration(logger, current_version)
    try:
        config.validate()
    except ValueError as error:
        logger.error(str(error))
        return 2

    service = PollingService(
        config=config,
        storage=Storage(config.db_path),
        gemini_client=GeminiClient(
            api_key=config.gemini_api_key or "",
            model=config.gemini_model,
            timeout_seconds=config.gemini_timeout_seconds,
            max_retries=config.gemini_max_retries,
            retry_delay_seconds=config.gemini_retry_delay_seconds,
        ),
        telegram_client=TelegramClient(
            bot_token=config.telegram_bot_token or "",
            channel_id=config.telegram_channel_id or "",
            parse_mode=config.telegram_parse_mode,
            timeout_seconds=config.request_timeout_seconds,
            max_message_chars=config.telegram_max_message_chars,
        ),
    )
    service.run_cycle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
