# API and worker

Backend-only package: no UI or Node installation is required. Read the [manager/team handoff](../HANDOFF.md). The API and worker behavior is unchanged; the original testing UI stays on the separate delivery branch.

Python 3.12. Run from `api-service/`. Create `.venv`, install `requirements-dev.txt`, copy `.env.example` to a private `.env` and set a local test token. SQLite is for local development only. API: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8100`; worker: `python -m app.worker`. Local Swagger: `/docs`.

Windows PowerShell setup, from this directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Edit private `.env`: retain `APP_ENV=development`, `AUTH_MODE=api_key`, `API_CLIENT_KEYS={}` and set a generated dedicated `APP_API_TOKEN`. Generate it locally with `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"`. Set `BROWSER_RECOVERY_ENABLED=false` for API-only local tests; enabling recovery requires a protected wrapping key. Never use production credentials/data for laptop testing. Transcription/minutes generation requires separately approved provider configuration; blank placeholders do not enable it.

Start API and worker in separate terminals using the same directory/configuration:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8100
# Second terminal, also in api-service/:
.\.venv\Scripts\python.exe -m app.worker
```

Open `http://127.0.0.1:8100/docs`; protected calls need `Authorization: Bearer <local-token>`. Ctrl+C stops each process. On Linux use `python3.12 -m venv .venv`, install with `.venv/bin/python -m pip install -c requirements.linux.lock -r requirements-dev.txt`, then use `.venv/bin/python` for the same API/worker commands. Docker installs pinned Linux runtime dependencies itself.

Production uses company Oracle from day one. See [deployment](deploy/README.md), [API contract](docs/guides/API_CONTRACT.md), [current readiness](docs/READINESS.md) and [qualification plan](docs/QUALIFICATION_TEST_PLAN.md).

Validation:

```text
python scripts/verify_release.py --backend-only
python scripts/check_api_contract.py
python -m scripts.audit_dependencies --backend-only
python scripts/check_deploy_template.py
python scripts/smoke_http.py
```

Verification and dependency audit are always backend-only in this branch; `--backend-only` remains accepted for compatibility. `--quick` runs critical regressions rather than the full suite. Compose checks require Docker Compose. `scripts/smoke_web.py` is the shared Python fixture imported by the API gateway smoke; it does not ship or build a UI.

After building the backend/gateway images, run `scripts/smoke_gateway.py` and `scripts/smoke_docker.py` for isolated synthetic Linux checks. These do not certify actual Oracle, identities, providers or production capacity. Enable `git config core.hooksPath .githooks` from the repository root; the hook runs critical backend checks and needs this project's virtual environment. CI creates its own environment; Node is not required.

`python scripts/build_handover.py --backend-only` creates `dist/handoff-api-service-package.zip` and its `.sha256` file. The source ZIP includes source-commit provenance and per-file checksums, excluding UI, secrets, local data and dependencies. Rebuilding provenance requires a Git checkout; an extracted service can install and run without Git. Deploy immutable backend/gateway images with protected configuration. Never commit `.env`, wallet/key files, databases, audio, installed dependencies or generated reports.
