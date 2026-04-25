from __future__ import annotations

import time

import requests


class PostbackClient:
    def __init__(self, url: str) -> None:
        self.url = url
        self.enabled = bool(url)

    def send(self, payload: dict[str, object]) -> tuple[bool, int | None, int]:
        if not self.enabled:
            return True, None, 0

        start = time.perf_counter()
        resp = requests.post(self.url, json=payload, timeout=10)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if 200 <= resp.status_code < 300:
            return True, resp.status_code, latency_ms
        return False, resp.status_code, latency_ms
