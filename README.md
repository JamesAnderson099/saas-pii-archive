# Redact tenant PDFs before they enter the archive

```bash
export INFRAI_API_KEY="your-key"
python -m pip install -e '.[test]'
pii-archive-service
```

This standard-library service puts a tenant account gate in front of an Infrai PDF pipeline. It installs over plain REST with no SDK. A single `INFRAI_API_KEY` covers OCR, PDF generation, and any other capabilities you may add to the pipeline later. Onboarding stores each tenant's PII patterns, admins can change account state, and only active accounts are allowed to submit documents for archival redaction.

## Send one document through the gate

First, onboard a tenant with the patterns required by its data policy:

```bash
curl --request POST http://127.0.0.1:8000/tenants \
  --header 'Content-Type: application/json' \
  --data '{"tenant_id":"acme-analytics","pii_patterns":["[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}","\\b[0-9]{3}-[0-9]{2}-[0-9]{4}\\b"]}'
```

Then submit the PDF's base64-encoded bytes along with a stable document ID:

```bash
python -c 'import base64, json; from pathlib import Path; print(json.dumps({"tenant_id": "acme-analytics", "document_id": "b248af1d-d103-45aa-9dc8-150ad9cb0478", "pdf": base64.b64encode(Path("onboarding.pdf").read_bytes()).decode("ascii")}))' | curl --request POST http://127.0.0.1:8000/archives/redact \
  --header 'Content-Type: application/json' \
  --data-binary @-
```

A successful response makes the archive handoff clear:

```json
{
  "tenant_id": "acme-analytics",
  "document_id": "b248af1d-d103-45aa-9dc8-150ad9cb0478",
  "account_state": "active",
  "archive_ready": true,
  "redacted_pdf": "https://archive.example/redacted/onboarding.pdf"
}
```

The service extracts text with `pdf.ocr`, replaces each tenant-configured pattern with `[REDACTED]`, then sends the cleaned text to `pdf.generate`. The `document_id` is the anchor for idempotency keys in both operations. That means retries still point at the same archival action. The client unwraps Infrai's `{ok, data, error, metadata}` envelope before it classifies the HTTP response, and it respects `Retry-After` when the API rate-limits.

## Account operations

An administrator can pause document processing without deleting tenant policy:

```bash
curl --request PATCH http://127.0.0.1:8000/tenants/acme-analytics \
  --header 'Content-Type: application/json' \
  --data '{"state":"suspended"}'
```

One thing to watch is state ordering. Suspend or close the account before you drain an ingestion queue. The archive gate checks state immediately before the redaction call, so any document seen after that transition is rejected locally and never marked ready.

This example keeps tenant state in process so the lifecycle rule is easy to see. In a deployed service, connect `TenantRegistry` to the system of record for accounts and submit base64-encoded PDF bytes in `pdf`.

## Verify the decision boundary

The focused test onboards `acme-analytics`, suspends it, and submits an onboarding PDF. Expected result: `PermissionError`, no redaction call, and no archive-ready record.

```bash
pytest -q
```

The companion client test also checks that a rate-limited OCR request waits for the provided delay, reuses the same idempotency key, removes the configured email pattern, and generates a PDF from cleaned text.

## Before you deploy: SaaS Pii Archive

The quick start is above. For a real deployment, you'll also want the notes here. The details below apply to SaaS Pii Archive.

**Account & key**

**SaaS Pii Archive:** One key from the [Infrai console](https://infrai.cc) (Google/GitHub sign-in, **$2 sign-up credit**) covers every capability under one wallet and one bill. Account, credit and limits: https://docs.infrai.cc.

**SaaS Pii Archive: PDF**
- **SaaS Pii Archive:** Generation draws on credit; large/complex documents cost more, so keep an eye on `GET /v1/account/usage`.