# API-only deployment and operating guide

Initial deployment: one Linux application VM with API, independent worker and optional API-only HTTPS gateway; external company Oracle and shared encrypted persistent audio. No UI/Node image or Redis. This is not an executed deployment or HA certificate.

## Build and configure

1. Build `docker build -t <backend> .` and `docker build -f deploy/Dockerfile.gateway -t <gateway> .` from `api-service/`. Scan exact images/FFmpeg, publish to the company registry and record immutable digests.
2. Copy `deploy/api.env.example` outside Git, supply approved actual Oracle/JWT/provider/policy settings and secret-file paths. Do not set approval flags without actual acceptance. Use TCPS/wallet policy as required. Runtime must reject SQLite/local auth in production.
3. DBA provisions the new Oracle schema at expected version `0002_production` using the approved initial-schema procedure. Runtime has DML access rather than schema-administration permission. Run `python -m scripts.check_database --connect` and `python -m scripts.check_release_inputs` in the candidate environment; connectivity alone is insufficient.
4. Set protected interpolation values BACKEND_IMAGE, GATEWAY_IMAGE, PRODUCTION_ENV_FILE, AUDIO_VOLUME, SECRET_DIRECTORY and TLS_DIRECTORY. All mount directories must already exist. API/worker UID 10001 and gateway UID 101 need correct permissions. Audio volume must contain `.meeting-volume`; do not silently create a fallback mount.
5. Validate `docker compose -f deploy/compose.api.yaml config --quiet` with those inputs, then deploy through company release controls. Do not publish resolved secrets/configuration.

## Budgets and boundaries

API pool is 5+5, worker override 2+0: up to 12 application connections before operational clients. Pools are per process. Driver connect/pool/call defaults are 5/10/30 seconds; DSN retries and multiple round-trips can exceed a single timeout. Approve total network/failure budgets with the DBA. Admission's separate thread lane is half the configured API pool ceiling (minimum one); it does not reserve connections or replace atomic shared quotas.

Only gateway HTTPS is public. API host mapping is loopback `127.0.0.1:18100` for an authorized existing monitoring agent. Public metrics are blocked; collector uses an admin JWT and must renew it. CORS is not authorization. Browser recovery is disabled in the API-only example; if enabled, recoverable wrapping-key generations are mandatory.

`/api/health` is asynchronous liveness. `/api/ready` checks ingestion dependencies and may report worker degradation with 200; `/api/processing-ready` returns 503 without a fresh worker. Docker health alone does not restart an unhealthy live process. NGINX resolves the API address at startup: reload/recreate gateway when recreating API. One VM is a single-host outage boundary; shared local audio does not support arbitrary multihost deployment.

## Monitoring and incidents

Install [alert rules](monitoring/alerts.yml) through company monitoring; [scrape example](monitoring/prometheus.yml) needs actual private target/credential policy. Observe request p95/p99/errors, admission queue/service, Oracle plans/locks/pool timeouts, jobs/worker/provider usage, CPU/RAM/OOM, disk/inodes/scratch/network, certificates and backup age. Verify actual delivered alerts and off-VM deadman monitoring. Process-local metrics require one API process per scrape target until aggregation is qualified. Logs must remain restricted and redacted.

For incidents preserve DB/audio/log evidence; check mount/Oracle/worker/provider/identity before restarting affected components. Never enable a development token as a production bypass, clear live claims, manually mark jobs completed or delete pending audio. `python -m scripts.reconcile_audio` is read-only; explicit `--apply` quarantines uncertain expired uploads and adjusts reservations. Review quarantine before deleting it. Provider rotation uses protected mounted secrets and controlled restart; retain required historical recovery keys.

## Backup, restore, restart and rollback

Oracle backup is DBA-managed RMAN/redo or approved equivalent coordinated with audio/key/config capture; the local ZIP backup utility rejects Oracle. Approve RPO/RTO first. Quiesce/drain writers for an initial common checkpoint, record Oracle recovery point/schema and source/image/config/key generations, capture audio checksums and encrypt/store copies off-VM. Restore into clean isolated Oracle/audio locations with retention off, recover keys, verify FKs/owners/approved snapshots/job ledger/checksums and measure service plus backlog recovery. `scripts.verify_recovery_manifest` checks audio paths/bytes/hashes; it is not a complete restore certificate.

Rehearse API/worker drain, restart and gateway refresh before users. SIGTERM stops new claims; configured drain/lease/deadline budgets must be tested. External calls may be billed twice after a crash; verify committed business effects remain consistent. Roll back only to a tested schema/data-compatible backend/gateway/config pair; otherwise use approved forward repair or the matched isolated restore procedure. Never blindly downgrade schema or assume a previous production release exists for this new service.

## Release acceptance

Execute the [qualification test plan](../docs/QUALIFICATION_TEST_PLAN.md). Required: real Oracle correctness/plans/concurrency/failure, 100-session SLO/soak/integrity, actual client/identity/provider/security acceptance, delivered alerts, off-VM restore and four-team signoff. Current [readiness](../docs/READINESS.md) remains GO WITH CHANGES. No deployment is authorized merely by a passing build.
