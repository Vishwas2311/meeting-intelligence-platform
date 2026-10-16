# Meeting Intelligence Platform — End-to-End Architecture & Implementation Blueprint


Version 2.0 FINAL PROPOSED ARCHITECTURE | Implementation-ready design; integration assumptions require validation | 16 October 2026


Objective: consented meeting capture → Azure Speech-to-Text → durable transcript → evidence-grounded LLM minutes of meeting (MoM) → editable internal form → approved, auditable submission.


## FINAL SOLUTION DECISION — Read first

**Primary experience:** React/TypeScript web meeting console; source selection and permission check; Start/Pause/Resume/Stop; true Azure Speech SDK continuous recognition using `recognizing` (interim) and `recognized` (final) events; incremental persistent transcript; on Stop, finalize/reconcile, send to the existing hosted LLM API for schema-validated minutes, allow user review, and then submit into the internal application's authenticated API.

**Existing AI infrastructure:** Azure Speech region/resource/keys plus hosted LLM endpoint/key already exist. No new LLM, SLM, Whisper installation, GPU model server, or speech model hosting is required. Azure *Fast Transcription* is **not live**; it is an optional file-based recovery adapter. Azure *Batch Transcription* is **not in the default live pipeline**.

**Privacy boundary:** Azure and the hosted LLM receive the meeting audio or transcript, respectively, so verify organizational permission, data region, private networking and retention. Never expose long-lived API keys in browser code. The second Speech key enables rotation; it does not double quota.

**Capture boundary:** The web microphone captures local mic audio, **not automatically remote Teams desktop participant audio**. For Teams-native post-meeting transcription use Microsoft Graph transcript access when the tenant allows it; for direct all-party live capture from Teams desktop plus tab-closure resilience, plan an approved managed desktop capture agent as an optional separately delivered component. Microsoft's current Teams media guidance explicitly does not recommend real-time media bots for ordinary AI meeting-agent scenarios; do not make a media bot the default architecture.

**Source-of-truth boundary:** Durable consented audio is the recovery source of truth and committed finalized transcript segments are the text source of truth. Local capture durability is verified independently from Azure STT health; a tab closure cannot preserve live capture with a browser-only recorder. If no recoverable audio exists for an outage, clearly mark missing spans instead of fabricating speech or a complete MoM.

**Default deployment:** React static frontend + FastAPI modular backend + independently restartable background worker on an existing CPU VM/container host, PostgreSQL database, private object storage and reliable work queue (or existing enterprise equivalents). No separate AI inference hosting. For direct browser-to-Azure SDK streaming, verify short-lived token refreshing, access controls and any custom/private endpoint reachability from users' devices. For higher network isolation, use server-side streaming with higher application audio bandwidth and operational cost.

### Minimum dependency register before coding

| Dependency | Already available | Required next verification |
| --- | --- | --- |
| Azure Speech resource | Region, endpoint, primary/secondary key | Resource type/tier, Speech SDK continuous-recognition connectivity, DNS/firewall/private endpoint, quotas |
| Azure Fast transcription | Existing REST endpoint | API version and region; file format/length; may be used only for recovery |
| Hosted LLM | Endpoint and API key | Provider protocol, model/deployment ID, max input/output context, JSON schema behavior, privacy |
| Capture | Not decided | Mic-only, supported browser tab/system audio, Teams Graph transcript, or approved desktop agent |
| Internal business form | Internal app exists | Exact field dictionary, authentication, validation rules, API operations, idempotency |
| Runtime/storage | To confirm | Existing VM, TLS domain, database, object storage, durable queue, backups, SSO and retention policy |

### Final approved runtime modes

1. **LIVE:** capture active + local audio chunks saved + Azure STT connected; show interim text; persist finalized text.
2. **RECORDING_ONLY:** audio capture and local persistence healthy, STT unavailable; show honest warning; queue untranscribed intervals.
3. **RECOVERING:** reconnect to real-time Azure for new speech; asynchronously upload historical audio to Fast transcription; de-duplicate results and keep proper timestamps.
4. **GAP_UNRECOVERABLE:** no audio source or persistence failed; show affected timestamp ranges; flag incomplete MoM.
5. **REVIEW_REQUIRED:** LLM structured MoM generated from known transcript only; human verifies decisions, owners and due dates; enforce form-field validation.
6. **SUBMITTED:** persist third-party receipt, immutable audit entry and de-duplication key.

### Recommended exact UI information architecture

**Dashboard:** new meeting button, last meetings, processing/failure badges, search and permissions. **Meeting preparation modal:** capture source, mic/device test, language, title, recording notice, consent check, retention class. **Live workspace:** persistent header REC indicator; separate capture/STT/sync badges; timer; mic meter; Start/Pause/Resume/Stop; live interim and final transcript; timestamped gap alerts; retry status; optional speaker chips; keyboard-accessible controls. **Processing screen:** finalize → synchronize → repair gaps → generate MoM → validate. **MoM editor:** editable summary, participants, discussion, decisions, action items with owner/due-date confirmation, missing-data warnings, clickable transcript evidence. **Submission preview:** target app field mapping, schema errors, approval checkbox, submit once, receipt. **History:** filter, playback where approved, exports, audit events, retention/deletion status.

### Target nonfunctional acceptance gates (design targets, not vendor guarantees)

| Test | Pass condition |
| --- | --- |
| 90–120 minute live meeting | Recognizer remains effective or controlled reconnect restores operation; no unnoticed missing spans |
| 10-minute Azure/network STT outage | Locally committed audio retained and reprocessed once connectivity returns; no duplicate transcript |
| Browser tab closed | Existing committed chunks survive; capture stops and UI explicitly records gap |
| Second Stop or Submit request | Idempotent: one finalization job and one target business record |
| Unknown speaker/owner | Field stays unresolved, review required |
| Audio capture lost | REC turns off; alarm is explicit; recording completeness never misrepresented |
| AI timeout or malformed JSON | Queue retry and reject invalid schema; never lose transcript |
| Unauthorized access | Object access denied across tenants/users; secrets masked |
| Speaker diarization | Optional and mode-limited; never treat numeric speaker label as verified identity |

---

## 1. Executive decisions


| Decision | Recommended default | Important boundary |

| --- | --- | --- |

| UI | React + TypeScript, accessible enterprise design, persistent header status, primary recording workspace | The red REC state appears only after actual capture + STT startup acknowledgement |

| Backend | FastAPI modular monolith + asynchronous worker | Separate API lifecycle from processing lifecycle |

| STT | Azure Speech SDK continuous recognition; pluggable adapter | Region, endpoint and key must match supported SDK connection method |

| Capture | Browser microphone for MVP; browser tab audio where supported; Teams Graph transcript when enabled | A mic button alone cannot capture remote Teams participants |

| Long meetings | Incremental transcript persistence; optional consented audio chunk archival, reconnect and repair | Do not hold entire meeting only in browser memory |

| MoM | LLM structured JSON with evidence references and abstention | No guessed speaker names, owners, dates or decisions |

| Form | Schema-driven mapping, user review, authenticated API submission | No irreversible submission without approved business policy |

| Deployment | Managed identity + Key Vault + Postgres + Blob + Service Bus (or equivalents) | Corporate network and data residency approval needed |



## 2. Non-goals and decisions still required


- No covert recording or bypass of meeting-platform consent/admin settings.

- No assumption that browser screen sharing always contains system audio; actual track presence must be checked.

- No assumption that Teams Graph supplies live audio; its transcript artifacts are a separate ingestion route.

- No assumption that a single STT session survives arbitrary network interruptions or unbounded duration.

- Unknown inputs before coding: exact Speech endpoint hostname/type and region; subscription tier; frontend deployment origin; Windows vs macOS/browser; Teams desktop vs browser; whether Teams Graph admin approval is possible; internal form API contract; preferred LLM endpoint; retention and identity policy.

## 3. Research-backed external constraints


| Constraint | Published or platform behavior | Design consequence |

| --- | --- | --- |

| Browser microphone | getUserMedia requires permission and secure context | Show explicit microphone permission and device selection |

| Browser meeting audio | getDisplayMedia systemAudio is only a hint; captured stream may contain no audio track | Offer source test and an honest unsupported-audio error; do not promise Teams desktop capture |

| Teams transcripts | Microsoft Graph transcript access depends on app permissions, admin settings and meeting availability | Optional Graph ingestion adapter; admin onboarding required |

| Azure continuous STT | Use streaming continuous recognition for extended speech rather than one-shot short utterance | Keep a recognizer active, consume final/interim events, reinitialize after failure |

| Azure real-time diarization | Published maximum 240 minutes per session (S0) | For longer sessions, plan rotation/recovery; speaker IDs across sessions may change |

| Azure fast transcription | <5 hours and <500 MB per file under published quotas | Suitable recorded-file recovery within API-specific limits |

| Azure batch transcription | 1 GB file maximum; 240 minutes per file with diarization | Chunk and reconcile beyond file/diarization limits |

| S0 concurrency | Default 100 concurrent real-time requests; F0 default 1 | Admission control and region/tenant quota awareness |



Official sources: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits ; https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia ; https://learn.microsoft.com/en-us/graph/api/onlinemeeting-list-transcripts?view=graph-rest-1.0 ; https://learn.microsoft.com/en-us/graph/teams-changenotifications-callrecording-and-calltranscript


## 4. Logical architecture and data flow


```

[React Meeting Workspace]
  | start/pause/resume/stop + device/source test
  | HTTPS / REST + WebSocket/SSE updates
  v
[FastAPI: Auth + Session State + Consent + STT Broker] ---> [PostgreSQL: meetings, events, transcript, drafts, audit]
  | short-lived Speech credentials / signed audio channel         |
  v                                                                  v
[Audio Source Adapter] ---> [Azure Speech STT] ---> [Final segment ingest + sequence/dedupe]
  | optional consented audio chunks                  |               |
  v                                                 v               v
[Blob storage encrypted]                       [Outbox / Service Bus] ---> [Background Processing Worker]
                                                              | normalize, chunk, extract evidence
                                                              v
                                                          [Approved LLM]
                                                              |
                                                          [MoM schema validation]
                                                              |
                                                 [Review UI + Editable Internal Form]
                                                              |
                                             [Submission Adapter + Idempotency + Audit]
                                                              |
                                                     [Internal Application API]

```



Speech subscription credentials stay on the backend. A browser-direct Speech SDK adapter receives a short-lived authorization token and a separate non-sensitive region/endpoint configuration; a backend-relay mode is preferred when network, policy or audio-archiving requirements demand it.


## 5. Capture source decision matrix


| Source | What can be captured | Recommended release |

| --- | --- | --- |

| Microphone only | Local user / in-room participants audible to mic; not guaranteed remote attendees | MVP baseline |

| Browser tab/system audio | Remote participants if browser/OS actually exposes audio, plus separate microphone stream | Pilot after browser/OS compatibility test |

| Teams Graph transcript | Post-meeting Teams-managed transcript and speaker information when enabled/authorized | Enterprise Teams path |

| Teams real-time media bot | Authorized call/meeting media via specialized Teams APIs and hosting | Later phase; specialized deployment |

| User uploads audio | Meeting recording supplied with required permissions | Reliable fallback / recovery |



For browser mixed sources, Web Audio mixing may combine microphone and shared-tab tracks into mono PCM suitable for Azure Speech; avoid feedback and echo, verify levels with a 10-second self-test, and ensure chosen screen/audio permissions are clearly displayed. Browser capture may fail depending on Teams client and OS; never claim otherwise.


## 6. Azure credentials and settings (no secrets in frontend)


```

# Backend .env.template — placeholder names only, never commit real secrets
AZURE_SPEECH_ENDPOINT=https://<speech-hostname-or-custom-domain>
AZURE_SPEECH_REGION=<speech-region>
AZURE_SPEECH_KEY=<primary-key-from-key-vault>
AZURE_SPEECH_KEY_ALTERNATE=<secondary-key-from-key-vault>
AZURE_SPEECH_LANGUAGE=en-IN
AZURE_SPEECH_MODE=continuous
AZURE_SPEECH_AUTH_MODE=short_lived_token
AZURE_SPEECH_DIARIZATION_ENABLED=false
AUDIO_PERSISTENCE_ENABLED=false
BLOB_CONTAINER=<approved-audio-container>
DATABASE_URL=<managed-identity-or-secret-reference>
LLM_BASE_URL=<approved-llm-endpoint>
LLM_MODEL=<approved-model-id>
INTERNAL_FORM_API_BASE_URL=<approved-internal-app-api>


```



Primary/alternate speech keys are equivalent credentials to one resource, intended for rotation/recovery; NOT two simultaneous independent free tiers or doubled quotas. Key rotation: create/update secret version → deploy/refresh consumers → test → rotate previous key. Never send either key to a browser, console log, analytics, error payload, or LLM prompt. Region and endpoint must be validated: custom/private endpoints often require a different connection method than standard region-based SDK construction.


## 7. UX design system — premium, trustworthy recording control


Style: light canvas, navy text, restrained teal for completed processing, red reserved for ACTUALLY RECORDING, amber for degraded/partial; clean high-contrast cards; progress timeline; generous spacing. Keyboard-accessible controls, 44 px tap targets, screen reader live announcements, mobile-responsive sidebar collapse, reduced-motion support.


| Screen/section | Visible information | Primary interaction |

| --- | --- | --- |

| Dashboard | Recent meetings, status, duration, source, search/filter, drafts pending review | New meeting / resume review |

| Meeting setup sheet | Title, audio source, language, microphone device, permission test, participants notice checkbox | Test audio / Start |

| Active workspace | Sticky REC indicator, real elapsed time, input waveform, STT connected indicator, transcript, save status | Pause / Resume / Stop |

| Reconnect overlay | Audio local, STT network status, buffered seconds, attempt count, gap risk | Retry / switch source / stop safely |

| Processing screen | Steps: finalize, transcript QA, extract, merge, form map; progress with honest stages | Open transcript / retry eligible stage |

| MoM review | Summary, decisions, action owner/date, confidence flags, clickable timestamps for evidence | Edit / confirm / regenerate section |

| Form preview | Exact field mapping, required-field errors, diff from MoM, schema version | Validate / authorize submission |

| Submission receipt | Receipt ID, submitted-by, timestamp, target record link, immutable audit event | View record / export |



## 8. Detailed UI state machine


```

IDLE -> PREPARING -> AWAITING_PERMISSION -> CONNECTING_STT -> RECORDING
RECORDING <-> PAUSED
RECORDING|PAUSED -> STOPPING -> FINALIZING -> PROCESSING_MOM -> REVIEW_REQUIRED
REVIEW_REQUIRED -> SUBMITTING -> SUBMITTED
Any active state -> DEGRADED or ERROR_RECOVERABLE; terminal: FAILED, CANCELLED
STOPPING is idempotent: second click returns same active finalization job.
A valid RED REC indicator requires verified active audio capture and durable write health; live STT may independently be connected, reconnecting, or offline. Display STT and recording statuses separately.
Browser close during recording creates ABANDONED/RECOVERABLE session; never silently finalizes.


```



Persist state transitions server-side with version numbers (optimistic concurrency) and a unique meeting_id. Client timers are display-only: use server timestamps for actual duration. Start should not claim success on permission grant alone: establish durable recording before showing REC; if STT connection fails, indicate recording-only degraded mode rather than pretending live transcription is running. Pause must stop storing/transmitting audio (not only hide the UI); show explicit paused gap in transcript timeline.


## 9. Start, pause, resume and stop orchestration


1. Start: verify login/role → create session with idempotency key → choose input source → consent notice → browser permission → audio meter self-test → issue short-lived Speech token → start recognition → ack → show REC.

1. During capture: persist FINAL results with sequence IDs and timestamps. INTERIM text is volatile display-only and must not be counted as final. Heartbeats monitor audio track, network, browser visibility and token expiry.

1. Pause: stop/disable actual audio ingestion, flush final segment and record PAUSE_START. Resume: verify permissions + token/recognizer health, record PAUSE_END and continue timestamp base.

1. Stop: disable button, stop source tracks, flush pending finalized segments, wait bounded grace window, mark gaps/partial flags, complete transcript snapshot, enqueue MoM job and show status.

1. On abandoned tab or dropped client: server transitions to recoverable; persist available transcript. Never misrepresent missing audio as completed transcription.

## 10. Audio pipeline specifics


- Capture: source-aware, obtain audio-track proof; microphone is default. For mixed tab+mic capture use AudioContext resampling/mixing only after supported-source checks.

- Transport: prefer Azure SDK PushAudioInputStream/browser adapter if compatible; otherwise a versioned backend WebSocket PCM frame protocol. Avoid assuming compressed MediaRecorder webm/opus chunks can be injected unchanged into raw PCM stream.

- Normalize audio to a supported Azure Speech input format (often mono signed 16-bit PCM with declared sample rate). Record actual native rate and resampler metadata.

- For recovery, optionally persist small consented timestamped audio chunks to private Blob storage, encrypted and checksummed. Default retention off until policy is approved.

- Backpressure: bounded ring buffer, queue high-water alarms, graceful degradation; if a chunk is dropped create explicit gap record rather than fabricating transcript.

- Long sessions: recycle STT connection/tokens before mandated expiry, checkpoint time-offset mapping and speaker-ID continuity; re-transcribe missing spans from retained audio when authorized.

## 11. Transcript data model and quality controls


| Entity | Important fields |

| --- | --- |

| Meeting | id, tenant_id, owner_id, title, meeting_source, language, started_at, ended_at, status, consent_version, retention_policy, version |

| AudioChunk | id, meeting_id, segment_start/end_ms, object_uri, sha256, format, sample_rate, consent_scope, deleted_at |

| TranscriptSegment | id, meeting_id, source_session_id, provider_result_id, seq, start_ms, end_ms, speaker_id, final_text, confidence_if_available, status |

| TranscriptGap | meeting_id, from_ms, to_ms, cause, recovery_status |

| MomJob | id, meeting_id, transcript_revision, prompt_version, model_id, status, attempts, started_at, completed_at |

| MomVersion | id, meeting_id, schema_version, structured_json, rendered_text, reviewer_id, review_status, evidence_map |

| FormSubmission | id, meeting_id, mom_version_id, target_system, idempotency_key, payload_hash, target_record_id, receipt, status |

| AuditEvent | id, tenant_id, meeting_id, actor, action, request_id, timestamp, redacted_metadata |



Uniqueness: (meeting_id, provider_result_id) or a deterministic segment hash plus source-session and sequence; segments arrive out of order and interim recognition may revise text. Late results should update only the same logical result before transcript snapshot sealing.


## 12. LLM processing: grounded MoM, not free-form hallucination


1. Freeze a transcript revision; assess coverage, detect gaps, redactions, overlap, language and speaker attribution limitations.

1. Chunk by time and topic with overlap; extract structured decisions, action items, risks and quotes, each referencing segment IDs/time ranges.

1. Merge chunk extractions, deduplicate tasks and reconcile conflicting owners/due dates. Preserve ambiguity; unresolved fields remain null.

1. Generate final MoM with title, objective, attendees (if verified), concise summary, discussion, decisions, actions, deadlines, follow-ups, evidence references.

1. Validate JSON schema using Pydantic; verify every evidence ID exists, every date is explicit or user-entered, required fields satisfied; mark unverified items as needs_review.

1. Persist MoM version and model/prompt version; allow regenerate only selected sections without overwriting user-approved edits.

```

{
  "meeting_id":"mtg_123", "summary":"Release readiness reviewed",
  "decisions":[{"text":"Finish regression tests before release", "evidence_segment_ids":["seg_14"]}],
  "action_items":[{"task":"Complete automation testing","owner":null,
    "due_date":null,"needs_review":true,"evidence_segment_ids":["seg_19"]}],
  "transcript_quality":{"has_gaps":false,"speaker_names_verified":false}
}

```



Never use the transcript as instructions to change model system prompt, authorize form submission or exfiltrate data. Transcript content is untrusted task data. Enforce tool allowlists and strict JSON output handling.


## 13. Internal form integration and submission contract


1. Discover/define target form schema (field names, types, enums, required fields, length limits, allowed values, dependencies, maximum payload size and auth scopes).

1. Create mapping contract: MoM field → target field, transformation, provenance, default policy, validation rule, human-editability and rejection reason.

1. Show preview and side-by-side source evidence; block submission when owner/deadline/required fields are unknown or when important transcript gaps exist.

1. Use service-to-service authentication with least privilege; submit with idempotency key derived from tenant, meeting and approved MoM revision.

1. On timeout: query target by idempotency key or external correlation ID before retry; never create duplicate form records simply because the response was lost.

1. Persist target record ID and immutable submission receipt; provide repair flow for rejected submissions and explicit human-initiated resubmission.

Prefer documented internal application APIs. UI automation is a fallback requiring a separate approval, secrets handling, stable selectors, monitoring and human checks.


## 14. API contract proposal


| Method / path | Purpose / behavior |

| --- | --- |

| POST /v1/meetings | Create idempotent session with title/source/language and consent version |

| POST /v1/meetings/{id}/prepare | Preflight media + STT connectivity; returns non-secret setup data |

| POST /v1/meetings/{id}/start | Guarded state transition; ack only when recognizer starts |

| POST /v1/meetings/{id}/pause / /resume | Pause/resume with timestamped capture gaps |

| POST /v1/meetings/{id}/stop | Idempotently finalize and queue processing |

| POST /v1/meetings/{id}/speech-token | Short-lived authorized token; never return subscription key |

| WS /v1/meetings/{id}/events | Live status/interim/final text and health events; resume from event_id |

| GET /v1/meetings/{id}/transcript | Paginated finalized segments and missing-audio intervals |

| GET /v1/meetings/{id}/mom | Current MoM version with evidence map |

| PATCH /v1/meetings/{id}/mom | Optimistic versioned corrections and review audit |

| POST /v1/meetings/{id}/mom/regenerate | Reprocess transcript or selected section with guarded revision |

| POST /v1/meetings/{id}/form/validate | Dry-run field validation and external target checks |

| POST /v1/meetings/{id}/form/submit | Idempotent approved submission and audit receipt |

| GET /v1/meetings/{id}/jobs/{job_id} | Processing stage, progress, retryable failure information |

| GET /v1/health/ready | Service readiness excluding sensitive config values |



API standard: X-Request-ID tracing, authz on every meeting resource, pagination, timestamps UTC ISO8601, structured error envelope, request size limits, optimistic concurrency, OpenAPI schema, and explicit provider error mapping.


## 15. Error taxonomy and exact user-facing messages


| Code / scenario | User-facing message | Recovery action |

| --- | --- | --- |

| MIC_PERMISSION_DENIED | Microphone access was denied. Enable it in your browser and try again. | Open browser instructions; retry setup |

| AUDIO_SOURCE_MISSING | No audio was detected from the selected meeting source. Choose another source or test your microphone. | Re-select/test; do not start false REC |

| REMOTE_AUDIO_UNSUPPORTED | Your browser cannot capture this meeting’s remote audio. Use a supported browser-tab source or Teams transcript integration. | Offer source switch / help |

| AUDIO_TRACK_ENDED | Audio sharing stopped. Recording is paused to avoid missing speech. | Reconnect source; annotate gap |

| STT_AUTH_FAILED | Speech service authentication failed. Contact support if this persists. | Refresh short-lived token; server logs detailed cause |

| STT_REGION_ENDPOINT_MISMATCH | Speech service configuration needs attention. Recording was not started. | Support diagnostic; verify endpoint-region pairing |

| STT_QUOTA_LIMIT | Too many transcription sessions are active. Please try later. | Queue/admission control + retry-after |

| STT_DISCONNECTED | Transcription connection was lost. We are reconnecting; part of the transcript may be missing. | Retry and buffer/recover |

| AUDIO_BUFFER_LIMIT | Audio could not be uploaded quickly enough. A gap may have occurred. | Save what exists, warn, reprocess retained chunks |

| SESSION_TOO_LONG | The current transcription session reached its supported duration. We are safely restarting the connection. | Session rollover; preserve offset |

| PARTIAL_TRANSCRIPT | The transcript has missing sections. Please review before generating the final MoM. | Show gap timeline and repair actions |

| LLM_TIMEOUT | MoM generation is taking longer than expected. Your transcript is saved. | Background retry / status |

| LLM_INVALID_JSON | The AI response could not be validated. We are retrying safely. | Limited retry and schema validation |

| MOM_UNVERIFIED_OWNER | An action item has no confirmed owner. Please select one before submitting. | Focus relevant form field |

| FORM_VALIDATION_FAILED | Some required form fields need correction. | Inline field errors |

| SUBMISSION_TIMEOUT_UNKNOWN | We could not confirm submission. Checking whether a record was already created. | Lookup before retry |

| SUBMISSION_REJECTED | The internal application rejected the form. Review the highlighted fields. | Show sanitized errors and retry after edits |

| SESSION_RECOVERY | This meeting ended unexpectedly. A partial transcript was saved. | Resume review or recover audio |



Error object: {code, message, recoverable, action_label, request_id, retry_after_seconds?, details?}. Never show Azure keys, stack traces, raw PII or internal URLs to ordinary users. Use accessible live regions and never auto-dismiss critical errors.


## 16. Retry policy, transactions and failure recovery


| Failure class | Retry approach | Safety note |

| --- | --- | --- |

| Transient Azure 429/5xx | Exponential backoff + jitter, bounded by retry-after, circuit breaker | Do not produce duplicate transcript segments |

| Azure 401/403 | Refresh credentials once; if still denied stop and alert | Never cycle keys blindly or expose secrets |

| Browser capture missing | Require new direct user permission gesture | Do not silently re-request screen capture |

| LLM timeout | Background bounded retries against immutable transcript revision | Avoid multiple contradictory MoM versions |

| Malformed model JSON | One schema-guided repair/retry then review-required error | Do not auto-submit unvalidated content |

| Internal app ambiguous timeout | Check target record/correlation ID before retry | Exactly-once outcome via idempotent adapter |

| Worker crash | Durable queue redelivery; outbox pattern and dedupe | Jobs must resume from checkpoints |



## 17. Security, privacy and enterprise controls


- Entra ID SSO and tenant-scoped RBAC: recorder, reviewer, submitter, administrator; separation of duties for sensitive workflows.

- Explicit participant notice/consent and organizational/legal policy checks; visible recording indicator; auditable consent artifact and revocation path.

- Key Vault primary/secondary Speech keys, managed identity where supported, safe token broker, no client-side subscription keys.

- Encryption in transit (HTTPS/WSS), encryption at rest, private containers, tenant scoping, optional private endpoints and data-residency validation.

- Configurable retention for raw audio, transcripts, MoM versions and audit. Delete requests cascade under approved retention/legal-hold rules.

- LLM prompt injection resistance: meeting transcript is untrusted text; cannot execute tools, change policies or trigger submission.

- Restrict logging to status/timing/error codes, redact credentials and PII, protect exported transcript links with expiring authorization.

- Never allow background tab processing or audio capture beyond clearly consented meeting scope; make Stop immediate and reliable.

## 18. Observability, SLOs and dashboards


| Area | Metrics/alerts |

| --- | --- |

| Frontend | Permission success, source-track availability, dropouts, mic silence, tab unload and user cancellations |

| Speech | Recognizer start success, time to first partial, final-segment rate, gap seconds, reconnect attempts, auth errors, 429s |

| Quality | Coverage % vs elapsed time, diarization unknown %, language shifts, duplicate/gap count, corrected transcription rate |

| LLM | Prompt/output tokens, latency, retry count, valid JSON %, evidence coverage %, reviewer edit rate |

| Form | Validation errors by field, submit success %, duplicate prevention count, mean time to receipt |

| Security | Unauthorized access, abnormal exports, secret rotation failures, retention/deletion compliance |



Initial proposed targets (to confirm with stakeholder): ≥99% successful start after approved permissions; ≥99% persistence of finalized segments; zero silent transcript gaps; 100% human review for unresolved owners/dates; zero duplicate form records. Measure end-to-end MoM completion time rather than promising instant results for multi-hour sessions.


## 19. Testing matrix / acceptance criteria


| Test | Expected behavior |

| --- | --- |

| Permissions denied/revoked mid-meeting | No false REC; clear error and recovery |

| Teams desktop with browser capture | Unsupported if remote audio track absent; do not claim full transcript |

| 30m / 60m / 180m capture | Segments save incrementally; stop reliably; no unreported gaps |

| 4h+ diarization boundary | Detect threshold; rotate/segment safely and reconcile speaker IDs |

| Lost Wi-Fi / sleep / tab close | Partial transcript saved; gap annotations; explicit recoverable status |

| Mic mute / other participant speech | Live source meter and coverage warnings; do not fabricate text |

| Duplicate recognizer events | Deduped segment records |

| Long multilingual meeting / noise | Quality warning; language configuration; reviewer corrections |

| Malicious transcript instructions | LLM treats text as data; submission permissions unchanged |

| LLM truncated JSON | Bounded retry then safe failure |

| Internal API timeout and double-click | Single target record or unknown-to-review state; never blind duplicate |

| RBAC + tenant isolation | Cannot read/write another user’s meeting |

| Key rotation during long session | Refresh or reconnect without exposing credential |

| Deletion request | Delete under retention policy with audit evidence |



## 20. Deployable repository structure


```

meeting-intelligence/
  frontend/
    src/app/ (routes, session, protected shell)
    src/features/meeting/ (controls, source-picker, status, transcript)
    src/features/mom/ (reviewer, evidence, editor)
    src/features/forms/ (mapping-preview, submission)
    src/shared/ (design-system, auth, api-client, error-catalog)
  backend/
    app/api/v1/ (meetings, sessions, speech_token, transcript, mom, submit)
    app/domain/ (state-machine, entities, schemas, policies)
    app/adapters/audio/ (browser_stream, file_upload, teams_graph)
    app/adapters/stt/ (azure_speech, result_normalizer)
    app/adapters/llm/ (provider_interface, structured_mom)
    app/adapters/forms/ (internal_api, validation)
    app/infrastructure/ (postgres, blob, queue, keyvault, tracing)
    app/workers/ (finalize, recover, mom, submission)
  infra/ (terraform-or-bicep, pipelines, env templates)
  docs/ (architecture, adr, API contract, privacy, runbooks, UI spec)
  tests/ (unit, contract, integration, browser-e2e, performance, chaos)


```



## 21. Delivery plan, priorities and dependencies


| Phase | Scope | Done when |

| --- | --- | --- |

| 0 — Integration spike | Verify Speech region/endpoint/sdk compatibility; demonstrate 2-minute audio capture from selected source; verify Teams constraints | STT transcript produced with real audio, no subscription key in browser |

| 1 — Recording MVP | React layout, Entra authentication, state machine, start/pause/resume/stop, live transcript, persistence, error catalog | 1-hour single-user recording survives basic disconnect tests |

| 2 — Reliable long meetings | Audio source test, optional chunk storage, session rollover, gap repair, recovery UI, observability | 90–180 min scenario and forced fault injection pass |

| 3 — MoM | Versioned grounded extraction, structured JSON, review editor, evidence navigation and export | Decisions/actions linked to transcript segments and accepted by reviewer |

| 4 — Form integration | Target schema mapping, validation, approved submit, idempotency and audit | Successful end-to-end form record created exactly once |

| 5 — Enterprise expansion | Teams Graph path, concurrency scaling, stronger retention, security tests, SLOs | Admin-approved deployment and production operations |



## 22. Integration checklist before development


- Speech endpoint URL/hostname (redact any credential-bearing query string)

- Speech resource region and pricing tier (F0/S0)

- Whether service uses default Speech resource or private/custom endpoint

- Primary and secondary key location in secret manager (never paste actual values)

- Primary meeting platform and client type (Teams browser/desktop, Zoom, etc.)

- Target users’ browser and OS (Windows vs Mac), recording consent policy

- Live transcript essential? Or post-meeting Teams artifact acceptable?

- Speaker diarization required? language(s), participant name matching requirements

- Approved LLM deployment (Azure OpenAI, other API/local) + context/output limits

- Internal application form schema, API access, auth and submission policy

- Transcript/audio retention and data residency requirements

- Target number of simultaneous meetings, expected max duration and budget

## 23. Architecture acceptance and next coding step


Approve capture-source strategy and field-schema contract before implementation. Start with a narrow proof of concept: microphone permission → Azure continuous STT with short-lived token → finalized transcript persistence → stop → structured MoM mock → editable form. Then add platform audio source and production hardening. The design intentionally does not invent your real endpoint URLs, secrets, LLM IDs or internal form fields.


## 24. Official reference links


https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits


https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech


https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-configure-azure-ai-speech-sdk


https://learn.microsoft.com/en-us/graph/api/onlinemeeting-list-transcripts?view=graph-rest-1.0


https://learn.microsoft.com/en-us/graph/teams-changenotifications-callrecording-and-calltranscript


https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia


https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia


## 25. Updated official research notes — October 2026

- **Azure Speech JavaScript recognizer:** official SDK recognizes continuously until stopped; interim events (`recognizing`) and final events (`recognized`) are first-class API features. https://learn.microsoft.com/javascript/api/microsoft-cognitiveservices-speech-sdk/speechrecognizer
- **Credentials:** authorization tokens must be refreshed on the existing recognizer before expiry, not merely updated on `SpeechConfig`. https://learn.microsoft.com/javascript/api/microsoft-cognitiveservices-speech-sdk/speechconfig
- **Azure quotas:** Standard S0 default real-time concurrency 100; real-time diarization max 240 minutes/session; Fast files under 5h and 500MB; Batch file max 1GB. Verify exact SKU and regional features. https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits
- **Private endpoints:** network/DNS custom endpoint changes affect SDK URI configuration. https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-private-link
- **Teams raw media:** Microsoft advises meeting-intelligence builders to prefer Graph meeting transcripts or Copilot Studio over specialized real-time media bots. Media bots require dedicated C#/.NET and Windows Server in Azure for production. https://learn.microsoft.com/en-us/microsoftteams/platform/bots/calls-and-meetings/real-time-media-concepts

**Document status:** architecture specified, not yet deployed or validated against the user's actual resource, tenant, audio source, LLM API and form schema.
