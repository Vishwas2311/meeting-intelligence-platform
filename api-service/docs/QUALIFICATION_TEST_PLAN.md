# UAT, Oracle, performance and production-signoff test plan

22 October 2026. Derived from the [findings/classification](READINESS.md) and [execution plan](../deploy/README.md). Current baseline is **82/100, GO WITH CHANGES**. All cases below are **NOT RUN** under this plan; historical local tests are supporting evidence only. Missing required environments/access mean BLOCKED, never PASS. No new Oracle/staging/production test has been executed by creating this document.

Scope: fresh Oracle-only API service, separate worker, API-only ingress, agreed initial VM, 100-session launch. No Redis, production React deployment, new features or architecture review. Consumer UAT uses the consuming team's existing client or an API test driver; our React harness is unchanged and not reviewed/tested here.

## 1. Entry conditions and fixtures

APP = Application Team; INFRA = Infrastructure Team; DBA = DBA Team; CONSUMER = Consumer Integration Team. Each case names the accountable team; supporting teams follow the execution plan. Assign an individual executor and reviewer before starting.

Before Oracle/integration qualification:

- Freeze source SHA, immutable backend/gateway digests, approved enabled API profile, redacted configuration fingerprint, Oracle version/service/schema identifier and expected schema head. Use the actual deployed authenticated OpenAPI contract; default local contract has 50 operations, but disabled optional recovery changes the effective inventory.
- INFRA/DBA provide an approved production-like isolated deployment and dedicated Oracle schema/audio mount. Fault tests, security probes, load and restore use isolated nonproduction targets only. Real Oracle opt-in tests require a disposable schema, not production or shared data.
- Prepare Alice/Bob test identities with distinct owners and one admin identity, plus expired/wrong-audience/wrong-permission fixtures. Load needs 100 distinct permitted identities and token renewal for the soak. Obtain real identity/provider access only through protected configuration; none is assumed available now.
- Fixtures: uniquely prefixed synthetic meetings, consent-on/off requests, stable event IDs, valid encoded audio and SHA-256 ledger, conflicting duplicate bytes, multilingual/emoji/blank notes, >32KiB JSON CLOBs, 1,001/10,000-segment ID sets within supported caps, representative owner histories/job backlogs, supported maximum-sized uploads, reviewed expected minutes/evidence and deliberately invalid provider responses. Never send the load script's placeholder audio bytes to a real transcription service.
- APP/DBA agree exact expected status/body/invariants against the deployed contract before execution. Preserve accepted event IDs/chunk hashes/job IDs/owner/revision/approved snapshot expectations in a restricted ledger.
- Sign provider spend/throughput caps, job/drain/overload recovery deadlines, total DB/DSN failure budget, notification deadline, RPO/RTO, accuracy/quality rubric and resource budget **before** testing. Unknown criteria block the affected case; do not invent pass criteria after observing failure.

Do not put secrets, tokens, DSNs with credentials or meeting content in shared reports. Raw evidence remains in protected company storage; shared evidence contains sanitized summaries and hashes.

## 2. Run order and checkpoints

1. INFRA/DBA establish candidate, initial Oracle schema, network/mount/secrets and basic health. Run ORA-01/02 and one thin ingest-to-result smoke.
2. APP/DBA execute remaining Oracle and interruption/failure cases; CONSUMER/APP run UAT. INFRA runs exact-artifact security and prepares monitoring/backup in parallel on independent targets.
3. APP runs PERF-01 diagnostic ramp. Fix demonstrated failures with configuration/capacity or narrow compatible patches; rerun impacted cases before expensive acceptance.
4. Run PERF-02/03/04 on a quiet acceptance host. Do not run security scans, backup I/O, competing suites or fault injection concurrently on that host. Run PERF-05 faults separately.
5. Finish isolated recovery/release rehearsals and all signoffs. Promote the same digests, then run only nondestructive production smoke before admitting users.

No 500/1,000/5,000/10,000 qualification is required for this launch or claimed by it. Schedule depends on provisioning/failures; minimum acceptance includes the 40-minute ramp/hold and a separate two-hour soak, excluding preparation, bursts, drain and reruns.

## 3. Evidence and result rules

Every case record must include: case ID, owner/executor/reviewer, status (NOT RUN/BLOCKED/PASS/FAIL/N/A), source/image/config/profile IDs, environment/schema identifier, fixture/ledger version, exact procedure/command, start/end times with timezone, expected and actual outcomes, sanitized evidence location/hash, defect IDs and retest dependencies. N/A is allowed only for an explicitly disabled/out-of-launch capability with signed scope justification; it cannot waive a mandatory gate.

Evidence abbreviations below:

- **HTTP:** redacted requests, response statuses/headers and assertions, correlation/job IDs; no bearer values or private payloads.
- **INV:** expected-versus-actual DB/audio/owner/revision/job/quota ledger and checksum reconciliation after completion/recovery.
- **OBS:** time-aligned API, admission queue/service, pool/Oracle waits, worker/provider and host metrics plus sanitized logs.
- **REL:** exact candidate/config/schema identifiers, inspections/scans/approval records.

A 2xx response or a queue acknowledgement alone does not prove processing success or durable integrity. All accepted work must reconcile to a terminal result or a documented recoverable state within the case's approved deadline. Timed-out/partial tests are not PASS. Fixes require root-cause evidence and affected retesting; material image/configuration changes require relevant release and smoke checks again.

## 4. UAT / integration cases

These validate the API service and actual consumer contract, not our local React implementation. Consumer is accountable for business acceptance; APP supports protocol/data assertions. Cases map to G8/G9/G11 and integrity gates G2/G5.

| ID / owner | Execution steps | Success criteria | Evidence |
| --- | --- | --- | --- |
| UAT-01 / CONSUMER | Fetch actual authenticated schema; compare enabled routes, media, revisions/errors and async job semantics with the consumer. Exercise one authenticated request through actual ingress. | Consumer accepts deployed profile/version and successful/error/media contracts; no hidden dependency on our UI. | HTTP; signed route/profile matrix. |
| UAT-02 / CONSUMER | Attempt create without consent, then create with consent; start, submit capture status, transcript batches, valid audio chunks/manifest, stop and fetch history/transcript/coverage. | Missing consent rejected; valid lifecycle follows allowed states; timestamps/events/chunks/owners match ledger; invalid transitions rejected per contract. | HTTP, INV. |
| UAT-03 / CONSUMER | Queue generation, poll to terminal state within agreed deadline; fetch minutes; review, edit with expected revision and approve. Attempt stale edit and alteration of approved content; exercise existing explicit revision flow. | Queue ACK is asynchronous; correct terminal result; stale revision conflict; approved snapshot immutable; permitted revision preserves original approval evidence. | HTTP, INV; reviewed synthetic result. |
| UAT-04 / CONSUMER | Create personal recap; save/review notes; exercise recap audio/finalization/selection and minutes flows when enabled. | Existing notes/transcript preserved until explicit selection; supported edits/finalization complete; generated output retains personal-recollection uncertainty. | HTTP, INV; fixture/result review. |
| UAT-05 / CONSUMER | On synthetic transcript loss, run enabled recovery/improvement workflow, compare original/candidate, retain original then separately adopt a valid candidate; attempt stale adoption/approved-record change. | Original evidence not silently overwritten; only authorized valid adoption succeeds; stale/forbidden changes rejected; correct terminal jobs. | HTTP, INV. |
| UAT-06 / APP | Repeat identical event/chunk IDs and bytes, then send same ID with conflicting content; interrupt/replay consumer transport. | Identical retries do not duplicate committed records or quota; conflicting retries rejected; acknowledged records and checksums preserved. | HTTP, INV; retry ledger. |
| UAT-07 / APP | Deliberately slow chunk and recap writes; disconnect mid-body/after dispatch, cancel route task and terminate container during threaded upload. Restore/retry, then reconcile reservations/audio/quota. | No concurrent session teardown/use, truncated acknowledged file, duplicate commit or quota corruption; unacknowledged partial work safely reconciled within agreed deadline. | Fault timeline, HTTP, INV, OBS; reproducible harness. |
| UAT-08 / CONSUMER | Lose a create response after possible server commit; execute agreed consumer reconciliation/user-confirmation; test retry/backoff for 409/429/503 and token renewal during polling. | No blind replay of ambiguous create; existing non-idempotent-create semantics explicitly accepted; no uncontrolled request loop or lost acknowledged workflow. No new feature assumed. | Consumer behaviour trace, HTTP; signed retry contract. |
| UAT-09 / APP | Use Alice/Bob/admin across every enabled owner-scoped route, lists/downloads/jobs/keys and privileged management. Exercise expired/wrong issuer/audience/signature/permission tokens, rotation with overlap and JWKS outage. | Expected 401/403; no cross-owner data/mutation; admin cannot bypass meeting ownership; no API-key fallback; bounded safe identity outage. | Route/role/token matrix, HTTP, OBS; no credentials in artifacts. |
| UAT-10 / APP | With a spend cap, submit real encoded synthetic media/notes in supported languages/formats to approved Speech/LLM endpoints. Review evidence/citations/quality; inject 429/timeout/5xx/malformed responses through controlled doubles. | Agreed quality/turnaround rubric met; only supported outputs accepted, no invented approval, bounded retries/checkpoints/concurrency/cost, safe failures and eventual drain. | Reviewed fixtures/results in protected storage, job/usage ledger, HTTP, OBS. |
| UAT-11 / CONSUMER | If browser recovery is enabled, test same-owner keys, unauthorized denial and supported key rotation/recovery; otherwise confirm route/config disabled and consumer does not call it. | Enabled capability works securely, or explicitly disabled profile accepted. No change to our React harness. | Profile decision, HTTP, key-custody acceptance if enabled. |

All required launch UAT cases must pass; disabled optional cases need signed N/A. UAT-07 is mandatory for enabled uploads. No further feature development is planned: an unmet feature requirement means scope/launch decision, not automatic implementation.

## 5. Oracle cases

DBA owns environment/plan/privilege cases; APP owns functional/concurrency assertions. These cover G1/G2/G3/G5/G13. Execute against the actual supported Oracle service, not SQLite or a compiler-only substitute.

| ID / owner | Execution steps | Success criteria | Evidence |
| --- | --- | --- | --- |
| ORA-01 / DBA | Verify version/service/charset/Thin/TCPS/wallet and runtime privileges. Prepare fresh expected schema; start API/worker with protected Oracle config. Run read-only database and release preflights in candidate context; negative-test wrong schema/config in isolation. | Intended Oracle only, expected head `0002_production`, preflights zero; wrong selection/head fails closed; no SQLite DB opened; runtime lacks unnecessary DDL/DBA privileges. | REL; redacted preflight/permission report. |
| ORA-02 / APP | Run both opt-in Oracle tests on approved disposable schema, then API/worker round-trips for all enabled features using real processes and Oracle admission/session factories. | Both Oracle tests execute and pass rather than skip; all counters/leases/business data use same intended schema; real-process smoke passes. | JUnit, HTTP, INV, configuration/selector proof. |
| ORA-03 / APP | Write/read >32KiB Unicode JSON CLOBs, emoji, blank/NULL notes, UTC/timezone/window boundaries and supported string/payload limits. Compare persisted/read values. | No encoding/truncation/type error; documented blank/UTC semantics; constraints and supported lengths enforced. | Fixture hashes, type/length assertions, INV. |
| ORA-04 / APP | Execute recovery/improvement lookups using 1,001 and 10,000 IDs within supported caps; page timelines/history and reconcile result IDs. | No Oracle expression/bind error; correct results/ownership/paging; no unexpected materialization/OOM. | HTTP/query assertions, INV, OBS. |
| ORA-05 / APP | Concurrent first-rate-bucket creation/increments, same-owner quotas and job submissions across distinct meetings; test window boundary and rejected attempts. | No limit bypass/duplicate counter row, correct Retry-After and committed counts; no unhandled race error; all accepted allowances accounted for. | Concurrency harness/version, status counts, DB counter/quota INV. |
| ORA-06 / APP | Race edit versus approval with same expected revision; race duplicate upload/event; exercise transactional rollback after business writes before commit. | Only permitted winner commits; stale response follows contract; original approved snapshot intact; rollback leaves no partial acknowledged effects. | HTTP, INV, transaction traces without payloads. |
| ORA-07 / APP | Use two isolated test workers to compete for jobs; exercise running/expired claim, provider and maintenance leases, heartbeat and atomic job completion. Restore launch worker count afterward. | Single committed business completion, safe stale-claim rejection, correct lease recovery; no lost accepted work. | Claim/token-state assertions, job effects/revision counts, INV, OBS. |
| ORA-08 / DBA | Seed representative history/segment/job backlog; capture actual execution plans/reads/waits for history, timeline, admission, runnable/leased jobs, audit/revisions and comparisons/search. Measure pool checkout/hold/timeouts under PERF-01/02. | Required SLOs supported; connection use within approved ceiling; no steady-state pool timeouts; expensive scans/locks understood and acceptable. Index existence alone is not PASS. | Actual plans/runtime statistics and query-to-endpoint mapping; pool budget and OBS. |
| ORA-09 / DBA | In isolation hold locks, interrupt network, restart DB/service, exercise stale sockets and rotate runtime credentials while API/worker work is active. Restore service and drain/reconcile. | Bounded failure within signed whole-operation/DSN budget; safe statuses; responsive liveness/honest readiness; heartbeat/lease recovery correct; no lost acknowledged or duplicate committed effects. | Fault timeline, HTTP, OBS, INV; measured recovery. |
| ORA-10 / DBA | Create matched Oracle/audio/key/config recovery point; encrypt/store off-VM, restore into clean isolation with retention off, verify and run API/worker reconciliation. Include corrupt/missing audio and representative >20GiB dataset per recovery plan. | Actual RPO and service/backlog RTO meet signed targets; owners/FKs/approvals/jobs/audio checksums reconcile; key recovery works; corruption rejected. | Backup/restore receipts, checkpoint IDs, INV, timed RPO/RTO record. |

### Existing Oracle test entry point

After DBA approval and private test configuration, from `api-service/` use the environment's Python executable:

```text
python -m pytest -q tests/test_oracle_integration.py tests/test_oracle_concurrency.py --junitxml=<restricted-evidence-path>/oracle-tests.xml
```

`ORACLE_TEST_URL` must be supplied privately and `ORACLE_TEST_SCHEMA_CONFIRMED=true` explicitly set for a disposable schema. These tests write data and may initialize an empty test schema. Do not target production/shared schema. Existing tests cover only part of this plan; they do not replace ORA-01–10 or actual-process deployment tests. Use the installed virtual-environment Python; do not paste passwords into commands/reports.

## 6. Performance cases

APP is accountable; INFRA/DBA provide host/Oracle measurements, CONSUMER confirms realistic request mix. Reference: [existing load procedure](../load/README.md). Launch mix: 60% browsing, 20% ingest, 20% processing; 100 active sessions does not mean 100 simultaneous transcriptions. Realistic provider delay, fixture sizes/history and offered rates must be recorded.

| ID | Steps | Pass criteria / evidence |
| --- | --- | --- |
| PERF-01 diagnostic ramp | Run controlled 1/10/50/100-user ramps on Oracle with fast then realistic-duration synthetic providers; measure generator/event-loop/transport, endpoint timings, admission queue/service, SQL/commit/pool/locks, worker/host I/O. Stop and diagnose clear integrity/saturation faults before soak. | No integrity errors; root cause of any missed SLO separated into app/config/DB/host/provider/generator. Evidence: raw offered/achieved rates and OBS. This diagnostic is not final capacity acceptance. |
| PERF-02 launch ramp/hold | On a quiet production-equivalent stack and independent generator: 10-minute ramp to 100 users, 30-minute hold, controlled ramp-down. Seed representative histories, 1,000/10,000-segment cases within caps and realistic audio sizes. | All thresholds below pass for defined operation groups/measurement windows; no hidden generator saturation or dropped work. Raw generator/settings + HTTP/OBS/INV required. |
| PERF-03 two-hour soak | Separate run: ramp to 100, hold for two hours with token renewal, realistic delays and retained data growth; ramp down and reconcile. Do not silently alter quotas or suppress expected client activity to keep test running. | Same SLO/integrity criteria, stable queue/resources, no increasing memory/connection/reservation leak; approved usage/storage budgets respected. Evidence: trend metrics, token-renewal record, actual mix and final INV. |
| PERF-04 finish burst / drain | Submit 10 processing jobs across sufficient owners in two minutes so approved per-owner allowances are respected; poll until terminal, then compare expected work. Use realistic service time and approved provider limits. | Queue ACK SLO, no quota bypass/duplicate/lost output; queue drains within signed provider/workload-based deadline and all jobs reconcile. Evidence: enqueue/start/end ledger, queue/service/usage OBS. No invented fixed job-completion SLO. |
| PERF-05 isolated overload / recovery | Separately exceed approved rate/concurrency, inject worker/API restart and DB/provider faults; stop excess arrivals and measure recovery. Do not combine fault traffic with nominal SLO acceptance. | Explicit bounded overload responses/backoff; no uncontrolled memory/spool/cost growth or integrity loss; recovery/drain within signed budget. Evidence: fault/arrival timeline, expected throttle classification, OBS/INV. |
| PERF-06 supported maxima | Exercise agreed maximum chunk/recap/text/segment/comparison/history inputs and concurrent upload caps; also send over-limit/chunked bodies. | Supported data succeeds within applicable agreed budgets; above-limit data rejected safely; no OOM/quota corruption/spooling beyond configured caps. Evidence: fixture sizes, status/limit assertions, host/DB/queue measurements, INV. |

### Performance acceptance thresholds

| Metric | Required acceptance |
| --- | --- |
| Ordinary read/poll API | p95 <500ms; ordinary API p99 <1.5s |
| Queue acknowledgement | p95 <1s; separate from background job completion |
| Upload requests | p95 <2s for approved representative fixture/arrival mix; record upload sizes/network |
| Unexpected errors | <0.5% on nominal acceptance workload; expected negative/throttle probes run separately and are reported, not hidden |
| Integrity | Zero lost acknowledged events/audio/jobs, cross-owner access or approval corruption; every accepted job reconciled |
| Shared admission | p95 <50ms and <10% ordinary-request budget per existing load plan; record queue versus execution rather than substituting means for p95 |
| Queue/pools | No sustained queue growth at steady state; no steady-state pool timeouts; finish-burst drain within signed deadline |
| Resources | No OOM or unbounded growth; approve CPU/RAM/disk/scratch/generator budgets before run. Existing proposed conservative targets: CPU <70%, RAM <75%, disk/scratch >25% headroom. Define sampling/aggregation windows in run configuration. |
| Provider/recovery | Meet signed spend/concurrency/service and overload-recovery budgets; missing approval blocks acceptance |

Publish thresholds separately per operation and time window; whole-run averages cannot hide hold/soak degradation. Record histograms/raw samples and achieved request mix, generator CPU and dropped iterations. A closed-user test with stalled clients is not proof of capacity; supplement with a fixed-offered-rate diagnostic to identify queue growth/generator limits. Do not attribute a measured SQLite wait distribution to Oracle.

### Load-tool preparation and limitations

The existing `load/scenario.js` is a starting harness, not complete signoff automation. Its generic `browse` tag covers multiple operations and it lacks some dedicated acceptance coverage, such as a separate explicit poll threshold and the full finish-burst/recovery ledger. Before PERF-02, APP must verify endpoint classification/thresholds and add a qualification wrapper or test-only cases for all criteria above. Preserve case/version evidence and avoid mislabeling create latency as processing acknowledgement.

Supply `APPROVED_TEST_TARGET=true`, approved `BASE_URL`, private absolute `IDENTITY_FILE` and `USERS=100` through the test runner; configure `RAMP`/`HOLD` for each case and run k6 with `load/scenario.js` from the approved test host. Token files stay outside the repo with restricted access. The script's placeholder media tests ingest only; real transcription qualification uses valid encoded fixtures through a separate approved workload. Do not use `load/local_smoke.py` as the Oracle certificate: it intentionally creates disposable SQLite databases.

## 7. Production-signoff and release cases

Acceptance faults/scan/recovery occur in isolation. Final production checks below are nondestructive, restricted synthetic smoke before user admission. INFRA owns release/operating verification, APP owns application verification, DBA owns database/recovery, CONSUMER owns UAT acceptance.

| ID / owner | Execution steps | Success criteria | Evidence |
| --- | --- | --- | --- |
| SIGN-01 / APP | Match source SHA/images/config/profile/schema to ORA/UAT/PERF results; verify full backend/contracts and Linux API/worker/gateway release checks on final artifacts. | No unexplained candidate drift; relevant retests complete after changes; Oracle qualification tests actually executed, not skipped. | REL, JUnit, contract/gateway/runtime outputs, retest matrix. |
| SIGN-02 / INFRA | Inspect actual secret delivery, UIDs/read-only/capabilities/mount marker/durable audio, firewall/egress/CORS/TLS/private management. Exercise body/upload limits and missing/read-only mount on isolated deployment. | Approved configuration, valid TLS, no raw public management/metrics/secrets, safe limits/mount failure, restart durability and matching checksums. | Redacted inspections, exposure/TLS/negative-test HTTP and INV. |
| SIGN-03 / INFRA | Scan exact OS/native/FFmpeg/dependencies and source history; APP completes scoped authenticated API security assessment using actual roles/profile. Triage/retest. | No unaccepted release-blocking security finding or active exposed credential; complete stated coverage. Partial scan is not PASS. | Digest-matched scan/coverage reports, restricted secret disposition and remediation retests. |
| SIGN-04 / INFRA | Activate private metrics/logging/token renewal and alert routing; trigger latency/queue/worker/DB/provider/disk/backup incidents and off-VM deadman. | Metrics/logs safe, admin token renewal works, actual notifications reach named responders within signed deadlines; VM-loss visible externally. | OBS, notification receipts/timestamps, responder/runbook roster. |
| SIGN-05 / DBA | Review ORA-10 evidence and independently verify backup scheduling, off-VM copies, key custody and restore signoffs. | RPO/RTO accepted and met; recoverable matched Oracle/audio/key point and checked integrity. | Timed restore/backup report, custody references and INV; no secret material. |
| SIGN-06 / INFRA | Rehearse writer drain/restart/unhealthy-live-process response, gateway reload after API recreation and compatible known-good candidate rollback or approved recovery. | Bounded recovery, gateway reconnects, no unsafe duplicate effects or lost acknowledged work; usable procedure for a first release without inventing a prior production version. | Timed release/recovery runbook, digest/config compatibility record, HTTP/OBS/INV. |
| SIGN-07 / CONSUMER | Review UAT and sign enabled profile, retry/replay/revision/consent responsibilities, actual provider acceptance and known nonblocking limitations. | All in-scope UAT PASS; optional N/A justified; no missing mandatory feature treated as accepted automatically. | Signed UAT/contract matrix and defect dispositions. |
| SIGN-08 / INFRA | Obtain approved usage/cost/retention/holds/log-access/RPO/RTO/single-VM limitation decisions; name deputies/on-call/backup/key custodians. | No unknown mandatory policy or unowned duty; retention off until authorized; configuration approval flags reflect real approval. | Restricted policy approvals, budgets, owner/escalation roster. |
| SIGN-09 / APP | After promotion, before user admission: verify actual production digests/configuration parity, protected Oracle preflights, health/worker/readiness; perform one authorized isolated synthetic ingest-to-reviewed-result flow through ingress. INFRA checks TLS/mount/log/alert/backup scheduling; DBA checks actual service/runtime privileges. | Nondestructive smoke PASS; no production data exposed, exact intended Oracle and profile, real monitoring active; smoke fixture accounted for under approved retention. No load/fault/restore tests on live traffic. | Production REL/preflights, sanitized HTTP/INV and monitoring receipt. |
| SIGN-10 / APP | Assemble case/evidence index and obtain named APP/INFRA/DBA/CONSUMER approval; record final decision and conditional restrictions. | All mandatory cases/gates PASS; no open critical integrity/security/capacity/recovery blocker; permitted residual risks have explicit owners/acceptance. | Four-team release record, case ledger, accepted limitations and final exit decision. |

## 8. Findings-to-tests coverage

| Existing gate | Cases that close it |
| --- | --- |
| G1 Oracle correctness | ORA-01–04, SIGN-09 |
| G2 concurrent integrity | ORA-05–07, UAT-03/06/07 |
| G3 plans/pools | ORA-08, PERF-01/02/03/06 |
| G4 latency/capacity | PERF-01–06, final INV |
| G5 failures/recovery | ORA-09, UAT-07, PERF-05, SIGN-06 |
| G6 host/storage | SIGN-02/06/09 |
| G7 secrets/exposure | SIGN-02/03/09 |
| G8 identity | UAT-09, SIGN-03 |
| G9 providers/cost | UAT-10, PERF-03/04/05, SIGN-08 |
| G10 exact security | SIGN-01/02/03 |
| G11 consumer integration | UAT-01–11, SIGN-07 |
| G12 operating monitoring | SIGN-04/09 |
| G13 restore | ORA-10, SIGN-05 |
| G14 release/restart | ORA-09, SIGN-06/09 |
| G15 policies/ownership | SIGN-07/08/10 |

## 9. Failure handling and final exit

On FAIL: preserve evidence, identify root cause and classify application defect versus infrastructure/configuration/provider/consumer issue. Assign the responsible team and affected retests. Code changes are conditional, not planned features: upload lifetime, worker stall, Oracle query/transaction/adapter/auth defects may need narrow fixes if demonstrated. Configuration, sizing, secret/identity registration, consumer handling and operational automation may instead resolve the failure. Rebuild changed artifacts and rerun relevant correctness/security/performance cases; do not reuse stale candidate results.

Immediate stop/escalation: owner leak, lost acknowledged data, approval corruption, active secret disclosure, destructive action outside approved target, uncontrolled provider spend or test-induced resource exhaustion. Do not keep stressing a compromised integrity state to collect latency numbers.

**GO-LIVE READY requires:** all mandatory ORA/UAT/PERF/SIGN cases PASS, signed N/A only for disabled optional scope, matched final candidate evidence, no unresolved release-blocking findings, actual delivered alerts and tested RPO/RTO, consumer acceptance and four-team signoff. Numeric 90+ readiness is reassessed from those results, never awarded for writing the plan. Until execution, status remains **82/100 GO WITH CHANGES**.

Suggested case result template:

```text
Case ID / gate IDs:
Owner / executor / reviewer:
Status: NOT RUN | BLOCKED | PASS | FAIL | N/A
Source SHA / backend digest / gateway digest:
Config fingerprint / enabled profile / schema version:
Environment / Oracle service-schema identifier:
Fixture-ledger ID / procedure-tool version:
Start/end timestamp and timezone:
Expected result / actual result:
Evidence protected location / sanitized summary / SHA-256:
Defect and remediation / affected retest IDs:
Owner approval / residual risk or N/A scope approval:
```

This document adds test planning only. It does not change application behaviour, testing UI or deployed infrastructure, and it does not assert any new live test pass.
