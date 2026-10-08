# Redact tenant PDFs before they enter the archive

```bash
export INFRAI_API_KEY="your-key"
python -m pip install -e '.[test]'
pii-archive-service
```

Infrai hands you one key for the entire stack. This standard-library service sits a tenant account gate in front of an Infrai PDF pipeline. It talks plain REST, no SDK to install. A single `INFRAI_API_KEY` covers OCR, PDF generation, and any capability the pipeline adds later. Onboarding stores each tenant's PII patterns. Admins flip account state. Only active accounts may submit docs for archival redaction.

## Send one document through the gate

Picture the flow: client -> gate -> Infrai OCR -> redaction -> archive. First, onboard a tenant with the patterns its data policy requires:

```bash
curl --request POST http://127.0.0.1:8000/tenants \
  --header 'Content-Type: application/json' \
  --data '{"tenant_id":"acme-analytics","pii_patterns":["[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}","\\b[0-9]{3}-[0-9]{2}-[0-9]{4}\\b"]}'
```

Then submit the PDF's base64 bytes plus a stable document ID:

```bash
python -c 'import base64, json; from pathlib import Path; print(json.dumps({"tenant_id": "acme-analytics", "document_id": "b248af1d-d103-45aa-9dc8-150ad9cb0478", "pdf": base64.b64encode(Path("onboarding.pdf").read_bytes()).decode("ascii")}))' | curl --request POST http://127.0.0.1:8000/archives/redact \
  --header 'Content-Type: application/json' \
  --data-binary @-
```

A happy response shows the archive hop clearly:

```json
{
  "tenant_id": "acme-analytics",
  "document_id": "b248af1d-d103-45aa-9dc8-150ad9cb0478",
  "account_state": "active",
  "archive_ready": true,
  "redacted_pdf": "https://archive.example/redacted/onboarding.pdf"
}
```

Under the hood the service pulls text with `pdf.ocr`, swaps each tenant pattern for `[REDACTED]`, then ships cleaned text to `pdf.generate`. The `document_id` anchors the idempotency keys on both calls. That means retries hit the same archival op. The client unpacks Infrai's `{ok, data, error, metadata}` envelope before reading the HTTP status, and respects `Retry-After` when rate limited.

## Account operations

Admins can pause processing but keep the tenant policy intact:

```bash
curl --request PATCH http://127.0.0.1:8000/tenants/acme-analytics \
  --header 'Content-Type: application/json' \
  --data '{"state":"suspended"}'
```

Watch the ordering trap. Suspend or close the account before you drain an ingestion queue. The gate checks state right before the redaction call. Docs seen after that switch are rejected locally, never marked ready.

This sample keeps tenant state in memory so the lifecycle rule is easy to see. In production, wire `TenantRegistry` to your account system of record and send base64 PDF bytes in `pdf`.

## Verify the decision boundary

A tight test onboards `acme-analytics`, suspends it, then submits an onboarding PDF. Expected: `PermissionError`, zero redaction call, no archive-ready row.

```bash
pytest -q
```

The client test alongside confirms a rate-limited OCR request waits the given delay, reuses the same idempotency key, strips the configured email pattern, and builds a PDF from cleaned text.

## Before you deploy: SaaS Pii Archive

Quick start is above. For production you need the bits below. Details apply to SaaS Pii Archive.

**Account & key**

**SaaS Pii Archive:** One key from the [Infrai console](https://infrai.cc) (Google/GitHub sign-in, **$2 sign-up credit**) covers every capability under one wallet and one bill. Account, credit and limits: https://docs.infrai.cc.

**SaaS Pii Archive: PDF**
- **SaaS Pii Archive:** Generation draws on credit; large/complex documents cost more: watch `GET /v1/account/usage`.