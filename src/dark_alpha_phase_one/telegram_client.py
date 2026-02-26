from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .message_formatter import (
    build_signal_keyboard,
    format_copy_levels_message,
    format_detail_message,
    format_signal_message,
    parse_callback_data,
)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
        self._last_update_id: int | None = None
        self._payload_by_trace: dict[str, dict[str, Any]] = {}

    def _endpoint(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def _post(self, method: str, body: dict[str, object], timeout: int = 10) -> requests.Response:
        return requests.post(self._endpoint(method), json=body, timeout=timeout)

    def send_json_card(self, payload: dict[str, object]) -> tuple[bool, int | None, int | None, int]:
        message, parse_mode = format_signal_message(payload)
        reply_markup = build_signal_keyboard(payload)
        trace_id = str(payload.get("trace_id") or "na")
        self._payload_by_trace[trace_id] = dict(payload)

        if not self.enabled:
            return True, None, None, 0

        start = time.perf_counter()
        resp = self._post(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            },
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        if 200 <= resp.status_code < 300:
            message_id = resp.json().get("result", {}).get("message_id")
            return True, resp.status_code, int(message_id) if message_id is not None else None, latency_ms
        return False, resp.status_code, None, latency_ms

    def poll_updates_once(self) -> None:
        if not self.enabled:
            return
        params: dict[str, object] = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1

        try:
            resp = self._post("getUpdates", params)
            if not (200 <= resp.status_code < 300):
                return
            data = resp.json()
            for update in data.get("result", []):
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self._last_update_id = update_id
                callback = update.get("callback_query")
                if isinstance(callback, dict):
                    self._handle_callback_query(callback)
        except Exception as exc:  # noqa: BLE001
            logging.warning("event=telegram_callback_poll_error err=%s", exc)

    def _answer_callback(self, callback_query_id: str, text: str) -> None:
        self._post("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text}, timeout=10)

    def _send_html(self, chat_id: str, text: str) -> None:
        self._post("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)

    def _handle_callback_query(self, callback: dict[str, Any]) -> None:
        callback_query_id = str(callback.get("id") or "")
        data = str(callback.get("data") or "")
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id") or self.chat_id)

        try:
            parsed = parse_callback_data(data)
            if parsed is None:
                self._answer_callback(callback_query_id, "未知操作")
                return

            action, symbol, trace_id = parsed
            payload = self._payload_by_trace.get(trace_id, {"symbol": symbol})
            if action == "copy_levels":
                self._answer_callback(callback_query_id, "已送出可複製價位")
                self._send_html(chat_id, format_copy_levels_message(payload))
                logging.info("event=telegram_callback action=copy_levels symbol=%s trace_id=%s", symbol, trace_id)
                return

            if action == "detail":
                self._answer_callback(callback_query_id, "已送出詳細資料")
                self._send_html(chat_id, format_detail_message(payload))
                logging.info("event=telegram_callback action=detail symbol=%s trace_id=%s", symbol, trace_id)
                return

            self._answer_callback(callback_query_id, "未知操作")
        except Exception as exc:  # noqa: BLE001
            logging.error("event=telegram_callback_error data=%s err=%s", data, exc)
            try:
                self._answer_callback(callback_query_id, "操作失敗，請稍後再試")
            except Exception:  # noqa: BLE001
                logging.warning("event=telegram_callback_answer_fail")
