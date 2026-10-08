import json
from urllib.request import Request

from pii_archive.infrai_pdf import HttpResult, InfraiPdfClient


def test_redaction_retries_ocr_then_generates_from_scrubbed_text() -> None:
    attempts = 0
    delays: list[float] = []

    def transport(request: Request) -> HttpResult:
        nonlocal attempts
        attempts += 1
        assert request.method == "POST"
        payload = json.loads(request.data)
        if attempts == 1:
            assert request.full_url.endswith("/pdf/ocr")
            assert request.headers["Idempotency-key"] == "archive:acme:doc-7:ocr"
            assert payload == {"pdf": "input.pdf"}
            body = {"ok": False, "data": None, "error": {"code": "RATE"}, "metadata": {}}
            return HttpResult(429, {"Retry-After": "3"}, json.dumps(body).encode())
        if attempts == 2:
            assert request.full_url.endswith("/pdf/ocr")
            assert request.headers["Idempotency-key"] == "archive:acme:doc-7:ocr"
            body = {
                "ok": True,
                "data": {"text": "Owner: ada@example.com"},
                "error": None,
                "metadata": {},
            }
            return HttpResult(200, {}, json.dumps(body).encode())
        assert request.full_url.endswith("/pdf/generate")
        assert request.headers["Idempotency-key"] == "archive:acme:doc-7:generate"
        assert payload == {"markdown": "Owner: [REDACTED]", "store": True}
        body = {"ok": True, "data": {"pdf": "redacted.pdf"}, "error": None, "metadata": {}}
        return HttpResult(200, {}, json.dumps(body).encode())

    client = InfraiPdfClient("test-key", transport=transport, sleep=delays.append)

    assert client.redact("input.pdf", [r"[\w.+-]+@[\w.-]+"], "archive:acme:doc-7") == {
        "pdf": "redacted.pdf"
    }
    assert delays == [3.0]
