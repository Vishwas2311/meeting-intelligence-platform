# Meeting Intelligence Platform

A React/TypeScript live-meeting workspace and a FastAPI/Python backend for Azure Speech continuous streaming, incremental transcript preservation, recorded audio chunk storage, LLM MoM generation, human review, and optional internal form submission.

## Local verification

Verified on 16 October 2026:

- Backend test suite: 5 tests passed; test fixtures disable Azure Speech credentials to avoid live cloud calls.
- Frontend production build: `npm run build` passed.
- Frontend tests: `npm test` passes 9 checks covering the local authentication proxy and the independent recording stopwatch.
- Azure OpenAI: the `gpt-6.1-sol` deployment generated meeting minutes from a synthetic transcript, validated against the application's schema.
- Browser checks passed for authentication, history, transcripts, summary generation, draft validation, editing, approval, and new-meeting reset using synthetic data.
- Desktop, mobile (390 px), and tablet (768 px) layouts were checked. Live audio capture was excluded from this synthetic test run.

Real credentials belong in local `.env` files. The repository includes sanitized `.env.example` templates; recordings, databases, and secrets are excluded.

UI update verified on 17 October 2026: recording controls and the live transcript share a single desktop viewport. Long transcripts scroll inside their pane; Follow latest releases when you scroll up. The stopwatch updates in tenths, freezes while paused, and keeps its stopped value. Browser checks used silent synthetic audio and mocked transcripts across five viewport sizes from 820×700 to 1920×870, plus a 390px mobile layout. The actual local backend connection check also passed. Live Azure Speech recognition was not retested in this UI pass.

The proposed MoM layout is available at `http://localhost:5173/mom-preview.html`
while the frontend development server is running. Its larger tabs, topic
selectors, decisions, and action cards use fictional sample data. See the
[MoM preview notes](docs/MOM_UI_PREVIEW.md) for what is implemented and what still
needs backend integration.

## What is implemented

- React dashboard, meeting status, microphone or supported browser tab audio selection, recording consent gate, Start/Pause/Resume/Stop, live provisional/final text, previous meetings, JSON MoM editor, approval and submission controls.
- Official Azure Speech **JavaScript SDK continuous recognition**, using short-lived credentials brokered by the backend; recognizes text while audio is streamed. Fast Transcription is NOT the live transcription API.
- Browser MediaRecorder saves ~5s audio chunks into IndexedDB then attempts authenticated uploads to FastAPI. The backend validates SHA-256 and stores chunks atomically. Finalized transcript events use a local pending queue until acknowledged by backend.
- FastAPI session state machine, actor-scoped REST operations, idempotent segment ingestion, audit trail, audio chunk inventory and advisory coverage endpoint.
- PostgreSQL (Docker Compose) or SQLite (local development), separate Python worker for LLM calls, MoM edits/approval, configurable internal form adapter disabled by default.
- Long transcript section extraction and consolidation, conservative retry logic for transient worker failures.
- Automated backend tests with no live Azure or LLM credentials.

## Not verified / remaining enterprise integration requirements

This is a runnable **integration build**, NOT certified production-ready. Specific capabilities require your real endpoints, browser and organization infrastructure to test:

1. Azure Speech cloud continuous streaming, token refresh, region, custom domain and network access still require valid resource credentials and a live capture test.
2. Reliable capturing of **all remote Teams desktop participants** is NOT achievable merely by browser microphone access. An approved desktop media recorder or Teams native transcription integration is separately required.
3. Browser closure stops browser audio capture. Locally committed chunks survive, but unsaved audio and missing meeting audio cannot be reconstructed.
4. Fast STT recovery adapter exists, but **full automatic missing-audio retranscription + timestamp/speaker reconciliation is not production-implemented**. Azure Fast Transcription's API version, formats, and endpoint must be verified. MediaRecorder WebM chunks are not necessarily independently decodable Fast STT files: assemble/convert into supported audio in a vetted recovery worker before submitting.
5. Device capability differences, STT session offset rollover, speaker diarization and simultaneous-source mixing require field testing; timestamps in live SDK can reset on reconnection, so production must maintain accurate global offsets.
6. Enterprise Entra ID / OIDC, secure multi-tenant user identity, access policies, schema migrations, managed audio blob storage, encryption/retention automation, full audit retention, durable production job scheduler and real disaster recovery are still organization-specific integration tasks.
7. Internal application's API field mapping must be provided; form submission is intentionally disabled by default. LLM endpoint currently expects OpenAI-compatible chat-completions JSON.
8. Microphone/tab capture and a complete recording-to-summary browser session still require validation with a working Speech resource.

## Windows quickstart

Requires Python 3.11/3.12, Node 20+, npm, modern Chrome or Edge, a microphone, network access to your approved Azure Speech resource and LLM endpoint.

Open PowerShell in the extracted folder:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Configure `APP_API_TOKEN` (long random secret for LOCAL TEST ONLY), `AZURE_SPEECH_REGION`, `AZURE_SPEECH_KEY`, `LLM_ENDPOINT`, `LLM_API_KEY`, and `LLM_MODEL` in `backend/.env`. For production use SSO/secret vault; never expose these keys in React.

Terminal 1 (from `backend`):

```powershell
uvicorn app.main:app --reload
```

Terminal 2 (from `backend`):

```powershell
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

Terminal 3:

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Visit http://localhost:5173. During local development, Vite loads `APP_API_TOKEN` from backend `.env` and authenticates API requests automatically. Open Configuration and click Test Backend to check the connection; no token needs to be pasted. Set frontend `VITE_API_URL` to your backend origin if its port differs from 8000, and restart both services after changing the token. Production builds retain the manual-token connection until SSO is integrated. Choose microphone, confirm consent, and click Start Meeting. Browser microphone access is permitted on localhost or HTTPS. Confirm **both** actual audio capture and Azure STT connection before assuming a complete transcript.

`GET http://localhost:8000/docs` is the API specification. Start/Pause/Stop endpoints update server state but the **browser** controls actual audio capture.

### Local test

```powershell
cd backend
pytest -q
```

### Docker backend option

Set `POSTGRES_PASSWORD` in a root `.env` file and populate `backend/.env`. Run `docker compose up --build`. The frontend still runs separately via `npm run dev`, or can be deployed to your organization's static hosting.

## Source layout

```text
meeting-intelligence-platform/
  frontend/src/       React app, Azure SDK live recognition, IndexedDB recovery spool
  frontend/.env.example
  backend/app/        API, models, settings, worker, provider adapters
  backend/tests/      automated API tests
  backend/.env.example
  docs/               architecture baseline, capture guidance, checklist
  compose.yaml        PostgreSQL, backend and worker
```

## Operational cautions

- Azure live STT processes speech while users talk; Fast STT is for completed recordings only and is not used to simulate live streaming.
- Primary/secondary Speech resource keys are for credential rotation, not independent quotas.
- Audio goes to Azure and transcripts go to the configured hosted LLM endpoint; obtain organizational permission and appropriate participant notices.
- Development bearer-token authentication is **not production SSO**.
- Do not enable the internal form submit flag until the approved API mapping and idempotent service credentials are in place.
