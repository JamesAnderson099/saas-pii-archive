from uuid import UUID

import pytest

from pii_archive.archive_service import (
    AccountChange,
    AccountState,
    ArchiveRequest,
    ArchiveWorkflow,
    TenantOnboarding,
    TenantRegistry,
)


class RecordingRedactor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], str]] = []

    def redact(self, pdf: str, patterns: list[str], operation_id: str) -> dict[str, str]:
        self.calls.append((pdf, patterns, operation_id))
        return {"pdf": "https://archive.example/redacted.pdf"}


def test_suspended_tenant_cannot_send_a_document_to_redaction() -> None:
    registry = TenantRegistry()
    redactor = RecordingRedactor()
    workflow = ArchiveWorkflow(registry, redactor)
    registry.onboard(
        TenantOnboarding(tenant_id="acme-analytics", pii_patterns=[r"\b\d{3}-\d{2}-\d{4}\b"])
    )
    registry.change_state("acme-analytics", AccountChange(state=AccountState.SUSPENDED))

    request = ArchiveRequest(
        tenant_id="acme-analytics",
        document_id=UUID("b248af1d-d103-45aa-9dc8-150ad9cb0478"),
        pdf="https://uploads.example/onboarding.pdf",
    )

    with pytest.raises(PermissionError, match="account is suspended"):
        workflow.redact_for_archive(request)

    assert redactor.calls == []


def test_active_tenant_policy_drives_redaction_and_marks_archive_ready() -> None:
    registry = TenantRegistry()
    redactor = RecordingRedactor()
    workflow = ArchiveWorkflow(registry, redactor)
    registry.onboard(
        TenantOnboarding(tenant_id="acme-analytics", pii_patterns=[r"\b\d{3}-\d{2}-\d{4}\b"])
    )
    document_id = UUID("b248af1d-d103-45aa-9dc8-150ad9cb0478")

    result = workflow.redact_for_archive(
        ArchiveRequest(
            tenant_id="acme-analytics",
            document_id=document_id,
            pdf="https://uploads.example/onboarding.pdf",
        )
    )

    assert result.archive_ready is True
    assert result.redacted_pdf.endswith("redacted.pdf")
    assert redactor.calls == [
        (
            "https://uploads.example/onboarding.pdf",
            [r"\b\d{3}-\d{2}-\d{4}\b"],
            f"archive:acme-analytics:{document_id}",
        )
    ]

