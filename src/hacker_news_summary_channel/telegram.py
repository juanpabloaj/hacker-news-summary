from __future__ import annotations

from html import unescape
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        channel_id: str,
        parse_mode: str,
        timeout_seconds: int,
        max_message_chars: int,
    ) -> None:
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.parse_mode = parse_mode
        self.timeout_seconds = timeout_seconds
        self.max_message_chars = max_message_chars
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, text: str) -> int:
        self._validate_text_length(text)
        payload = {
            "chat_id": self.channel_id,
            "text": text,
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        }
        result = self._post("sendMessage", payload)
        return int(result["message_id"])

    def edit_message(self, message_id: int, text: str) -> None:
        self._validate_text_length(text)
        payload = {
            "chat_id": self.channel_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": True,
        }
        self._post("editMessageText", payload)

    def _post(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        data = urlencode(payload).encode("utf-8")
        request = Request(f"{self.base_url}/{method}", data=data, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            LOGGER.warning("Telegram request failed with HTTP %s: %s", error.code, detail)
            raise RuntimeError(f"Telegram request failed with HTTP {error.code}") from error
        except URLError as error:
            LOGGER.warning("Telegram request failed with URL error: %s", error.reason)
            raise RuntimeError(f"Telegram request failed: {error.reason}") from error
        if not body.get("ok"):
            raise RuntimeError(f"Telegram request failed: {body}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"Telegram request returned unexpected payload: {body}")
        return result

    def _validate_text_length(self, text: str) -> None:
        effective_length = len(unescape(text))
        if effective_length > self.max_message_chars:
            raise ValueError(
                f"Telegram message length {effective_length} exceeds limit {self.max_message_chars}."
            )
