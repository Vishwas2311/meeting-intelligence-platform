# Optional testing UI: company laptop

This is the existing React/TypeScript testing UI, copied without source changes. It has its own `package.json` and `package-lock.json`. It is not deployed with the API service and is excluded from the backend ZIP and Docker build context. No UI deployment or new integration is included. Existing browser-test helpers live in `../tests/ui_bridge.py` and `../tests/ui_server.py`; browser automation needs additional Playwright browser installation and is optional.

Use the Vite development server for local testing. The existing proxy only accepts localhost API targets and keeps the API token on the local Node process. `npm run preview` and opening `index.html` directly are not substitutes for this setup. A built UI expects company identity configuration, which is outside this local setup.

## Requirements

- Company-approved Python 3.12 and Node.js 22 LTS with npm on PATH; Git for cloning.
- Approved access to Python/npm package registries, or company package mirrors. Follow company certificate/proxy requirements; do not disable TLS verification.
- Free local ports 8100 (API) and 5174 (UI). The worker is a separate Python process.
- Microphone/browser permission for recording. Azure speech and the LLM need separately approved provider configuration for their respective features; blank credentials do not enable them. Provider credentials belong only in the backend's private `.env`.

## One-time Windows PowerShell setup

From the cloned repository root:

```powershell
cd api-service
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `api-service/.env` locally. Keep the local development profile:

```dotenv
APP_ENV=development
AUTH_MODE=api_key
API_CLIENT_KEYS={}
APP_API_TOKEN=<your-generated-local-token>
LOCAL_CACHE_KEK=<your-generated-base64-32-byte-key>
```

Generate values locally using these commands; copy each output into its corresponding setting. Do not include the angle-bracket placeholders. Preserve `LOCAL_CACHE_KEK` between restarts so existing browser recovery keys remain usable.

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

Leave `DATABASE_URL` unset for disposable local SQLite, or use a dedicated approved local configuration. Production remains fresh Oracle only. Do not use production secrets or production data for laptop testing. Local database/audio files stay in ignored `api-service/var/`.

Install the UI dependencies:

```powershell
cd frontend
npm ci
```

The UI defaults to `http://127.0.0.1:8100`. Only if an explicit setting is needed, copy `.env.example` to `frontend/.env` and set `VITE_API_URL` to a localhost API origin. Never put secrets in `VITE_*` variables. The proxy reads `APP_API_TOKEN` from the parent `api-service/.env`; no token needs to be entered into browser code.

## Run locally: three terminals

Terminals 1 and 2 start in the cloned repository's `api-service` directory.

Terminal 1, API (creates the local development schema on startup):

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

Terminal 2, worker:

```powershell
.\.venv\Scripts\python.exe -m app.worker
```

Terminal 3, from the cloned repository root:

```powershell
cd api-service/frontend
npm run dev
```

Open `http://127.0.0.1:5174`. API documentation is at `http://127.0.0.1:8100/docs`. Stop each process with Ctrl+C. Restart the UI dev server after changing `.env`. Missing provider configuration can prevent transcription/minutes generation even when the UI and API start successfully.

## Optional local checks

From `api-service/frontend`:

```powershell
npm run typecheck
npm test
npm run build
```

The build checks compilation; `dist/` remains ignored and is not a production UI release. Keep `.env`, `node_modules/`, generated builds, recordings and browser-test output outside Git. This local UI cannot certify Oracle, hosted authentication, production load or disaster recovery.
