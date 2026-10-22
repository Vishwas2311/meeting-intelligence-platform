# Manager and team handoff - 22 October 2026

## Scope and status

This is the existing Meeting Intelligence backend packaged as an API service. It provides meeting/transcript/audio/notes operations, asynchronous jobs, generated minutes, revisions/edit/approval and administrative/operational endpoints. The independent worker processes durable Oracle-backed jobs and calls configured AI providers. HTTP 202 acknowledges queued work; consumers poll for completion.

Production uses fresh Oracle from day one. SQLite is only for development/testing; no production data needs transferring. Redis and the React UI are outside this package. The UI is preserved separately on `api-service-delivery`, which remains the repository default.

Source baseline: CI-verified commit `8fa56b6bf5277ec03778b72dabc8938c4829110f`. API/worker runtime, ORM/schema and production configuration are unchanged. Packaging removes UI source/helpers and two UI-launcher tests, makes verification/dependency audit backend-only by default, and adds provenance, checksums and these instructions. The API's 50-operation contract is preserved.

**Readiness remains 82/100, GO WITH CHANGES for integration; not GO-LIVE READY.** This package does not certify live Oracle, production load, hosted identity/provider integration, alerts or recovery.

## Artifacts and reproducibility

- Primary source: `handoff-api-service-package` branch. Record the exact accepted commit.
- Built artifact: `api-service/dist/handoff-api-service-package.zip` plus `.sha256`. CI uploads these and test evidence as the `handoff-api-service-package` artifact, subject to retention.
- `HANDOVER_METADATA.json` records source commit and working-tree state. Accept a clean committed build, not one marked `working_tree_modified: true`.
- `HANDOVER_SHA256.json` records per-file hashes. Windows: run `Get-FileHash .\handoff-api-service-package.zip -Algorithm SHA256` and compare with the supplied checksum. Linux: run `sha256sum -c handoff-api-service-package.sha256` from its directory.
- Rebuild from a Git checkout using `python api-service/scripts/build_handover.py --backend-only`. Git is required for rebuilding source provenance, not for running an extracted service.
- This ZIP is reviewed source, not an executable, prebuilt image or database backup. Build/scan immutable backend/gateway images from the accepted source and deliver through the company registry.

## Company inputs and owners

| Owner | Inputs and acceptance |
| --- | --- |
| Infrastructure Team | Host/container engine, registry/image scanning, DNS/TLS/ingress, network routes, encrypted audio mount, secrets custody, resource budgets, log forwarding and delivered alerts |
| DBA Team | Oracle version/service, driver connectivity, fresh schema setup, DDL setup identity separate from DML runtime identity, privileges, pool/session budget, plans/indexes/locking/failure tests, backup and measured restore |
| Application Team | Reproduce checks, build/record images, configure approved providers, validate contracts/API/worker behavior, run security/performance/integrity/failure checks and investigate measured failures |
| Consumer Integration Team | Per-user identity/actor mapping, upload/checksum/replay, polling/backoff, expected-revision edits, overload/error behavior, business UAT and signoff |

No existing hosted target, provider credentials, company secrets or identity registration are assumed present. Those require approved owners and configuration.

## Oracle go-live checklist

1. Confirm the actual Oracle version/service, DSN/port, Thin driver compatibility, DNS/firewall and company TCPS/wallet/CA policy from the deployment host.
2. Supply protected `DATABASE_URL=oracle`, Oracle identity/DSN and password-file settings using [the production template](api-service/deploy/api.env.example). Production must reject SQLite and development authentication.
3. DBA initializes the fresh schema with the approved setup identity and `python -m alembic upgrade head` from `api-service/` using protected Oracle configuration. Expected head is `0002_production`; runtime identities must not have schema-administration rights.
4. Verify tables, constraints and indexes and capture actual Oracle plans. Offline compilation is insufficient.
5. Run `python -m scripts.check_database --connect` and `python -m scripts.check_release_inputs` inside the candidate runtime with the real audio mount/configuration.
6. Execute opt-in Oracle integration/concurrency tests only against an approved disposable schema, following [qualification procedures](api-service/docs/QUALIFICATION_TEST_PLAN.md). Never enable test cleanup against production.
7. Validate commits/rollbacks, owner isolation, claim contention, expired leases, edit/approval races, timeouts, reconnect and Oracle outage recovery through API/worker calls.
8. Approve total connection budgets: reference API 5+5 and worker 2+0, per process, plus operating clients.
9. Pass the Oracle-backed 100-session ramp/hold/soak and integrity checks with measured p95/p99, admission/SQL/pool waits and queue/provider behavior. Earlier SQLite latency results do not qualify Oracle.
10. Rehearse coordinated off-host Oracle/audio/key/config recovery with checksums, measured RPO/RTO and backlog recovery.

## Deployment and operations checklist

- Follow [the operating guide](api-service/deploy/README.md). Provision the host, Oracle connectivity, persistent audio and approved protected profile first. A Linux physical server or VM can use the reference Compose recipe; a different container platform needs validated configuration.
- Build/scan backend and API-only gateway images and record immutable digests. Company ingress can replace the gateway only after matching and testing HTTPS, upload/body limits, timeouts, protected routes and safe logs.
- Run API and worker continuously against the same Oracle schema/audio mount. Arbitrary multihost audio sharing and automatic HA are not provided.
- Mount secrets read-only; verify non-root permissions, `.meeting-volume`, restricted raw API access, private metrics and administrative token renewal.
- Configure approved JWT issuer/audience/JWKS, Speech/LLM endpoints, quotas and accepted policies. Template placeholders deliberately do not approve launch.
- Check `/api/health`, `/api/ready` and `/api/processing-ready`. Ingest readiness can remain available while processing is degraded.
- Prove client auth/authorization, provider success/timeout/retry, safe logs, delivered alerts, restart/drain/gateway refresh, compatible rollback and recovery.
- Application, Infrastructure, DBA and Consumer owners execute and sign [the qualification plan](api-service/docs/QUALIFICATION_TEST_PLAN.md). CI/local synthetic providers alone cannot approve production.

## Acceptance evidence

Record source/ZIP checksums, exact image digests, dependencies/scans, redacted configuration identifiers, schema/privilege proof, Oracle plans/concurrency/outage results, contract/HTTP/security evidence, 100-session performance/invariants, provider/consumer UAT, delivered alerts, measured restore/RPO/RTO and named four-team signoff. Keep credentials, wallets, recordings, transcripts and restricted logs outside Git and this package.

Use the latest successful **API service handoff checks** run for this branch and accepted commit. The reference baseline's 244 passing tests included two UI-launcher tests deliberately absent here. Oracle tests remain opt-in until an approved environment is available. See [readiness](api-service/docs/READINESS.md) for the open production gates.
