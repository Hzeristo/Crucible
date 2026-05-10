"""Telegram notification adapter for daily summaries."""

from __future__ import annotations

import logging
from typing import Any

import requests
from tenacity import RetryCallState, retry, stop_after_attempt, wait_exponential

from src.crucible.core.config import ChimeraConfig

logger = logging.getLogger(__name__)


def _log_before_retry(state: RetryCallState) -> None:
    if state.outcome is None:
        return
    exc = state.outcome.exception()
    if exc is None:
        return
    logger.warning(
        "[Notify] Telegram send failed at attempt %s/%s; retrying due to %s: %s",
        state.attempt_number,
        5,
        type(exc).__name__,
        exc,
    )


def _swallow_retry_error(_: RetryCallState) -> None:
    logger.error("[Notify] Telegram send exhausted all retries; skip notification.")
    return None


class TelegramNotifier:
    """Best-effort Telegram sender."""

    def __init__(self, settings: ChimeraConfig) -> None:
        self._bot_token = (
            settings.tg_bot_token.get_secret_value()
            if settings.tg_bot_token is not None
            else None
        )
        self._chat_id = (
            settings.tg_chat_id.get_secret_value()
            if settings.tg_chat_id is not None
            else None
        )
        if not self._bot_token or not self._chat_id:
            logger.warning(
                "[Notify] Telegram bot token/chat id missing; notification will be skipped."
            )

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=_log_before_retry,
        reraise=False,
        retry_error_callback=_swallow_retry_error,
    )
    def _send_summary_with_retry(
        self, html_message: str, reply_markup: dict[str, Any] | None = None
    ) -> None:
        if not self._bot_token or not self._chat_id:
            logger.warning("[Notify] Telegram credentials missing. Skip sending.")
            return

        api_url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": html_message,
            "parse_mode": "HTML",
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        response = requests.post(api_url, json=payload, timeout=15)
        response.raise_for_status()
        logger.info("[Notify] Morning broadcast successfully transmitted to BB Channel.")

    def send_summary(
        self, html_message: str, reply_markup: dict[str, Any] | None = None
    ) -> None:
        try:
            self._send_summary_with_retry(
                html_message=html_message, reply_markup=reply_markup
            )
        except Exception as exc:
            logger.error("[Notify] Failed to send Telegram summary notification: %s", exc)
