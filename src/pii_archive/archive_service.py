from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from .infrai_pdf import InfraiError, InfraiPdfClient


class AccountState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


@dataclass(frozen=True)
class TenantOnboarding:
    tenant_id: str
    pii_patterns: list[str]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TenantOnboarding:
        tenant_id = str(value["tenant_id"])
        patterns = [str(item) for item in value["pii_patterns"]]
        if len(tenant_id) < 3 or not patterns:
            raise ValueError("tenant_id and pii_patterns are required")
        return cls(tenant_id, patterns)


@dataclass(frozen=True)
class AccountChange:
    state: AccountState

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AccountChange:
        return cls(AccountState(value["state"]))


@dataclass(frozen=True)
class ArchiveRequest:
    tenant_id: str
    document_id: UUID
    pdf: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ArchiveRequest:
        return cls(str(value["tenant_id"]), UUID(str(value["document_id"])), str(value["pdf"]))


@dataclass(frozen=True)
class ArchiveResult:
    tenant_id: str
    document_id: UUID
    account_state: AccountState
    archive_ready: bool
    redacted_pdf: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "document_id": str(self.document_id),
            "account_state": self.account_state.value,
            "archive_ready": self.archive_ready,
            "redacted_pdf": self.redacted_pdf,
        }


class TenantRegistry:
    def __init__(self) -> None:
        self._tenants: dict[str, TenantOnboarding] = {}
        self._states: dict[str, AccountState] = {}

    def onboard(self, request: TenantOnboarding) -> AccountState:
        self._tenants[request.tenant_id] = request
        self._states[request.tenant_id] = AccountState.ACTIVE
        return AccountState.ACTIVE

    def change_state(self, tenant_id: str, change: AccountChange) -> AccountState:
        self.policy(tenant_id)
        self._states[tenant_id] = change.state
        return change.state

    def policy(self, tenant_id: str) -> tuple[AccountState, list[str]]:
        if tenant_id not in self._tenants:
            raise KeyError(tenant_id)
        return self._states[tenant_id], self._tenants[tenant_id].pii_patterns


class ArchiveWorkflow:
    def __init__(self, registry: TenantRegistry, redactor: Any) -> None:
        self._registry = registry
        self._redactor = redactor

    def redact_for_archive(self, request: ArchiveRequest) -> ArchiveResult:
        state, patterns = self._registry.policy(request.tenant_id)
        if state is not AccountState.ACTIVE:
            raise PermissionError(f"account is {state.value}")
        operation_id = f"archive:{request.tenant_id}:{request.document_id}"
        data = self._redactor.redact(request.pdf, patterns, operation_id)
        return ArchiveResult(
            request.tenant_id, request.document_id, state, True, str(data["pdf"])
        )


registry = TenantRegistry()
_workflow: ArchiveWorkflow | None = None


def workflow() -> ArchiveWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = ArchiveWorkflow(registry, InfraiPdfClient())
    return _workflow


class ArchiveHandler(BaseHTTPRequestHandler):
    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        return dict(json.loads(self.rfile.read(size)))

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        try:
            if self.path == "/tenants":
                request = TenantOnboarding.from_dict(self._body())
                state = registry.onboard(request)
                self._json(201, {"tenant_id": request.tenant_id, "state": state.value})
                return
            if self.path == "/archives/redact":
                result = workflow().redact_for_archive(ArchiveRequest.from_dict(self._body()))
                self._json(200, result.as_dict())
                return
            self._json(404, {"detail": "route not found"})
        except KeyError:
            self._json(404, {"detail": "tenant not found"})
        except PermissionError as error:
            self._json(409, {"detail": str(error)})
        except InfraiError as error:
            status = error.status_code if 400 <= error.status_code < 500 else 502
            self._json(status, {"detail": {"code": error.code, "message": str(error)}})
        except (TypeError, ValueError):
            self._json(400, {"detail": "invalid request"})

    def do_PATCH(self) -> None:
        tenant_id = urlparse(self.path).path.removeprefix("/tenants/")
        if not tenant_id or self.path == "/tenants/":
            self._json(404, {"detail": "route not found"})
            return
        try:
            state = registry.change_state(tenant_id, AccountChange.from_dict(self._body()))
            self._json(200, {"tenant_id": tenant_id, "state": state.value})
        except KeyError:
            self._json(404, {"detail": "tenant not found"})
        except ValueError:
            self._json(400, {"detail": "invalid request"})


def run() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8000), ArchiveHandler)
    server.serve_forever()


if __name__ == "__main__":
    run()
