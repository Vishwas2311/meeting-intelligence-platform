# API integration contract

Import the [published OpenAPI](openapi.json); the authenticated deployed `/api/openapi.json` reflects enabled features. Existing `/api` paths are v1; `X-API-Version: 1` identifies it. Additive optional fields are compatible. Local Swagger is `/docs`; the production gateway serves no SPA.

Use per-user Bearer JWTs in production. The backend validates RS256, issuer, audience, expiry and required permission. Ownership comes from the verified identity, not request fields. Admin access to global management does not bypass meeting ownership. Keep credentials/tokens out of logs and browser code.

Create with real consent, start, submit integer-millisecond transcript events and SHA-256-verified multipart audio, finalize the capture manifest, stop, queue generation and poll the returned job ID with bounded backoff. A queue acknowledgement is not a completed job or an approval. The consuming client owns microphone capture, consent, live Speech streaming where used, offline buffering, replay and user review. The API does not capture microphones or provide a live audio WebSocket.

Use stable client-event IDs and chunk sequence/checksum for retries. Identical duplicates are safe; conflicting content is rejected. Meeting creation has no caller idempotency key: do not blindly repeat an ambiguous create request. Reconcile state or request user confirmation. Honour Retry-After; do not enqueue processing on every poll.

Minutes edits accept `{data, expected_revision}` and the existing legacy flat form. Production edit/approve/revise require the returned revision; stale writes return 409. Approvals identify immutable reviewed snapshots. Client-side caches and replay must be owner-scoped and cleared/isolated on identity change.

History arrays use bounded limit/last-ID pagination. Transcript pages include segments, next_cursor and evidence_revision; fetch all pages and restart if evidence changes. Snapshots contain only an initial page. Use individual job polling and pagination for jobs/revisions/audit. Conditional minutes/transcript reads can return 304.

Errors preserve the existing `detail` forms, including validation arrays and structured conflicts. Native ingress 413/429 can be HTML: check status/media/Retry-After before parsing JSON. Audio, metrics and ZIP responses have their declared non-JSON media. Oracle production backup is a DBA operation; the local SQLite ZIP utility rejects Oracle.

Browser recovery is an optional configured capability, disabled by the API-only example. Agree it with the consumer and provide recoverable wrapping keys if enabled. Other enabled recap/finalization/recovery/improvement routes follow OpenAPI and require explicit review/adoption; do not silently replace original evidence.
