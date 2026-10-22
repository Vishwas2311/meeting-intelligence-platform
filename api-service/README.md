# API and worker

The optional [testing UI](https://github.com/Vishwas2311/meeting-intelligence-platform/blob/api-service-delivery/api-service/frontend/README.md) is a separate source folder for local laptop use. It is excluded from the production images and backend handover ZIP. API and worker behavior is unchanged.

Python 3.12. Run from `api-service/`. Create `.venv`, install `requirements-dev.txt`, copy `.env.example` to a private `.env` and set a local test token. SQLite is for local development only. API: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8100`; worker: `python -m app.worker`. Local Swagger: `/docs`.

Production uses company Oracle from day one. See [deployment](deploy/README.md), [API contract](docs/guides/API_CONTRACT.md), [current readiness](docs/READINESS.md) and [qualification plan](docs/QUALIFICATION_TEST_PLAN.md).

Validation:

```text
python scripts/verify_release.py --backend-only
python scripts/check_api_contract.py
python -m scripts.audit_dependencies --backend-only
python scripts/check_deploy_template.py
python scripts/smoke_http.py
```

After building the backend/gateway images, run `scripts/smoke_gateway.py` and `scripts/smoke_docker.py` for isolated synthetic Linux checks. These do not certify actual Oracle, identities, providers or production capacity. Enable `git config core.hooksPath .githooks` from the repository root; the hook runs critical backend checks and needs this project's virtual environment. CI creates its own environment; Node is not required.

`python scripts/build_handover.py --backend-only` creates a backend-only source ZIP under ignored `dist/`, excluding the optional UI and its browser-test helpers. Git source is the primary delivery; deploy immutable backend/gateway images with external protected configuration. Never commit `.env`, wallet/key files, databases, audio, installed dependencies or generated reports.
