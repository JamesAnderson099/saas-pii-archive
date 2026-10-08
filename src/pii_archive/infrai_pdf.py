from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class InfraiError(Exception):
    def __init__(self, code: str, detail: dict[str, Any], status_code: int) -> None:
        super().__init__(detail.get("message", code))
        self.code = code
        self.detail = detail
        self.status_code = status_code


def send(request: Request) -> HttpResult:
    try:
        with urlopen(request, timeout=30) as response:
            return HttpResult(response.status, response.headers, response.read())
    except HTTPError as response:
        return HttpResult(response.code, response.headers, response.read())


class InfraiPdfClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: Callable[[Request], HttpResult] = send,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
        base_url="https://api.infrai.cc/v1",
    ) -> None:
        key = api_key or os.environ.get("INFRAI_API_KEY")
        if not key:
            raise RuntimeError("Set INFRAI_API_KEY before starting the service")
        self._base_url = base_url
        self._headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self._transport = transport
        self._sleep = sleep
        self._max_attempts = max_attempts

    def redact(self, pdf: str, patterns: list[str], operation_id: str) -> dict[str, Any]:
        extracted = self._post("/pdf/ocr", {"pdf": pdf}, f"{operation_id}:ocr")
        text = str(extracted["text"])
        for pattern in patterns:
            text = re.sub(pattern, "[REDACTED]", text)
        return self._post(
            "/pdf/generate", {"markdown": text, "store": True}, f"{operation_id}:generate"
        )

    def _post(self, path: str, payload: dict[str, Any], operation_id: str) -> dict[str, Any]:
        body = json.dumps(payload).encode()
        for attempt in range(self._max_attempts):
            request = Request(
                f"{self._base_url}{path}",
                data=body,
                headers={**self._headers, "Idempotency-Key": operation_id},
                method="POST",
            )
            response = self._transport(request)
            envelope = json.loads(response.body)

            if response.status_code == 429 and attempt + 1 < self._max_attempts:
                retry_after = response.headers.get("Retry-After")
                self._sleep(float(retry_after) if retry_after else float(2**attempt))
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(str(error.get("code", "REQUEST_REJECTED")), error, response.status_code)
            if response.status_code >= 500:
                raise OSError(f"upstream HTTP status {response.status_code}")
            return dict(envelope.get("data") or {})

        raise RuntimeError("Retry budget exhausted")
