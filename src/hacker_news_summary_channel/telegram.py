from __future__ import annotations

from html import unescape
from http.client import RemoteDisconnected
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

POST_MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SECONDS = 30.0


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

    def delete_message(self, message_id: int) -> None:
        payload = {
            "chat_id": self.channel_id,
            "message_id": message_id,
        }
        self._post("deleteMessage", payload)

    def _post(self, method: str, payload: dict[str, object]) -> dict[str, object] | bool:
        data = urlencode(payload).encode("utf-8")
        body = self._request_with_retries(method, data)
        if not body.get("ok"):
            raise RuntimeError(f"Telegram request failed: {body}")
        result = body.get("result")
        if isinstance(result, bool):
            return result
        if not isinstance(result, dict):
            raise RuntimeError(f"Telegram request returned unexpected payload: {body}")
        return result

    def _request_with_retries(self, method: str, data: bytes) -> dict[str, object]:
        for attempt in range(1, POST_MAX_RETRIES + 1):
            request = Request(f"{self.base_url}/{method}", data=data, method="POST")
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                LOGGER.warning("Telegram request failed with HTTP %s: %s", error.code, detail)
                if attempt >= POST_MAX_RETRIES or error.code not in RETRYABLE_STATUS_CODES:
                    raise RuntimeError(
                        f"Telegram request failed with HTTP {error.code}"
                    ) from error
                delay = _retry_after_seconds(detail) or RETRY_DELAY_SECONDS * attempt
                LOGGER.warning(
                    "Retrying Telegram %s after HTTP %s on attempt %s/%s in %ss",
                    method,
                    error.code,
                    attempt,
                    POST_MAX_RETRIES,
                    delay,
                )
            except (URLError, TimeoutError, ConnectionResetError, RemoteDisconnected) as error:
                reason = getattr(error, "reason", error)
                LOGGER.warning("Telegram request failed with connection error: %s", reason)
                if attempt >= POST_MAX_RETRIES:
                    raise RuntimeError(f"Telegram request failed: {reason}") from error
                delay = RETRY_DELAY_SECONDS * attempt
                LOGGER.warning(
                    "Retrying Telegram %s after connection error on attempt %s/%s in %ss",
                    method,
                    attempt,
                    POST_MAX_RETRIES,
                    delay,
                )
            time.sleep(delay)
        raise RuntimeError("Unreachable retry state in Telegram client.")

    def _validate_text_length(self, text: str) -> None:
        effective_length = len(unescape(text))
        if effective_length > self.max_message_chars:
            raise ValueError(
                f"Telegram message length {effective_length} exceeds limit {self.max_message_chars}."
            )


def _retry_after_seconds(detail: str) -> float | None:
    try:
        body = json.loads(detail)
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    parameters = body.get("parameters")
    if not isinstance(parameters, dict):
        return None
    retry_after = parameters.get("retry_after")
    if not isinstance(retry_after, (int, float)):
        return None
    return min(float(retry_after), MAX_RETRY_AFTER_SECONDS)
