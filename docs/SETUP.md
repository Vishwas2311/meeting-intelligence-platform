# Setup and operations

Use Python 3.12 and Node.js 22 or later. From the project root:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
cd frontend
npm ci
cd ..
```

For a new installation only, copy `backend/.env.example` to `backend/.env`, generate a long random APP_API_TOKEN and configure provider settings. Never overwrite an existing `.env` during an update. Never copy keys into `.env.example`, frontend variables or Git.

## Environment

- APP_API_TOKEN protects backend requests. In local development Vite loads it server-side; no token entry is required in the UI. Restart services after editing `.env`.
- Azure Speech needs its current subscription key and matching region. The endpoint is an HTTPS origin. Fast recovery uses a full `transcriptions:transcribe?api-version=2025-10-15` URL in AZURE_SPEECH_FAST_ENDPOINT.
- LLM_ENDPOINT is the full chat-completions URL. OpenAI uses `https://api.openai.com/v1/chat/completions`; Azure v1 uses your resource's `/openai/v1/chat/completions`. LLM_MODEL is the provider model or Azure deployment name. v1 URLs do not need a dated LLM_API_VERSION.
- LLM_MAX_INPUT_CHARS defaults to 30000. This is a character bound, not a guarantee of quota availability. LLM_RESPONSE_FORMAT=json_schema enables strict output. Use json_object only when the provider lacks schema support. LLM_REQUEST_INTERVAL_SECONDS paces successful section calls; failure Retry-After is respected.
- DATABASE_URL/STORAGE_DIR may be omitted to use the local OS app-data directory. Explicit relative values resolve from backend/. Keep the live SQLite file outside OneDrive and other synchronized/shared folders.
- AUDIO_RETENTION_ENABLED=false keeps audio. If explicitly enabled, AUDIO_RETENTION_DAYS controls cleanup of verified, approved recordings while protecting recent/incomplete meetings. Transcripts and minutes remain retained. MAX_AUDIO_CHUNK_MB bounds individual uploads.

## Start and stop

Stop any existing recording and preserve pending browser audio before restarting.

```powershell
backend/.venv/Scripts/python.exe run.py start
```

On Windows, `start.cmd` runs the same supervisor. Open http://localhost:5173. Keep the supervising terminal open; Ctrl+C stops its children. From another terminal:

```powershell
backend/.venv/Scripts/python.exe run.py stop
```

The supervisor refuses a second instance and occupied ports. It initializes schema before starting the API/worker. Logs are in `%LOCALAPPDATA%/MeetingIntelligence/services` on Windows. `/api/health` reports the API process; `/api/ready` reports database readiness and a separate worker boolean. Worker false means queued jobs are saved but cannot currently progress.

## Updating the existing office laptop installation

1. Stop recording; allow pending uploads to finish. Keep the browser profile and site storage.
2. Stop API, worker and UI. Preserve `.env`, the SQLite database and uploaded audio. Replace source files only; do not copy another laptop's `.env` or runtime data.
3. Install pinned dependencies with pip and `npm ci` as above. The checked-in `backend/requirements.lock` constrains transitive versions.
4. To relocate an existing project-folder database, with services still stopped, run:

```powershell
backend/.venv/Scripts/python.exe run.py migrate-storage
```

This creates a verified full backup, restores to a new local-app-data runtime folder and updates `.env` storage paths. It refuses to overwrite an existing runtime. Original project files remain available for rollback. Never run migration while another copy is writing the source database.

5. Start with the supervisor. Existing schema upgrades add tables/job columns without resetting history. Refresh the browser after the source update. Browser outboxes upgrade in place.
6. Test a synthetic meeting: Start, verify live transcription, Pause/Resume, Stop, Generate MoM, edit, save, approve and reload. Open History and confirm previous records are still present.

## Recovery and provider failures

A Speech outage preserves local audio but marks the transcript incomplete. Stop and synchronize first, then choose Recover transcript. The worker decodes the saved recording and verifies a Fast Transcription result before permitting new minutes. Automatic recovery supports up to two hours and 250 MB of saved input audio. Old recordings without a finalized manifest or missing audio require manual recovery; no button fabricates missing speech.

429 responses use scheduled backoff, saved section checkpoints and readable job status. They can still fail when quota remains insufficient; adjust the provider deployment quota or wait for its reset. Smaller chunks do not by themselves increase your total token allowance. 401/403 requires matching current credentials/region. Never disable TLS verification to work around an office proxy: configure the organization's trusted certificate/proxy with IT.

## Backup and restore

Configuration ? Download full backup exports a verified SQLite/audio ZIP without `.env`. Keep meeting backups private. You can also create an archive from backend/:

```powershell
.venv/Scripts/python.exe -m app.storage backup C:/PrivateBackups/meetings.zip
```

Restore while the app is stopped, into an empty new folder:

```powershell
.venv/Scripts/python.exe -m app.storage restore C:/PrivateBackups/meetings.zip C:/MeetingRestore
```

The restore refuses an occupied destination, unsafe paths and checksum/integrity failures. Then set DATABASE_URL to `sqlite:///C:/MeetingRestore/meeting_intelligence.db` and STORAGE_DIR to `C:/MeetingRestore/audio` in your local `.env`, restart and verify the history. Credentials are deliberately managed separately. Never delete the original until you have checked the restored copy.

All backend history is retained beyond ten meetings. Only the browser read cache is limited to ten complete snapshots; pending uploads are separate. Clearing site data removes unsynchronized browser audio/text and the read cache. Changing localhost ports/origins creates a different browser storage area.

## Development checks

```powershell
cd backend
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m ruff format --check app tests
.venv/Scripts/python.exe -m pytest -q
cd ../frontend
npm run check
npm run build
npm run dev
```

With Vite running, from a second terminal in frontend/:

```powershell
npx playwright install msedge
node e2e/ui.mjs
```

The browser suite uses an isolated temporary API/database/worker, synthetic audio and deterministic providers. It never records the physical microphone or uses your provider keys. Results/screenshots go to output/playwright/negative. Windows CI runs the same suite. Live Azure/OpenAI qualification remains a separate test and should use short synthetic audio.

## Docker

`compose.yaml` supplies PostgreSQL, API and one worker with a shared audio volume. Set POSTGRES_PASSWORD outside the repository and configure backend/.env. API health must pass before the worker starts. Run the frontend separately. PostgreSQL/multi-user deployment, enterprise SSO and corporate audio/proxy policies require their own qualification.
