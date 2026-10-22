# Meeting Intelligence API service

API-service delivery branch with a separate, optional React testing UI for company-laptop use. Production delivers the Python/FastAPI API and independent worker with Oracle persistence. No Redis is required.

| Folder | Purpose |
| --- | --- |
| `api-service/app/` | API and background worker runtime |
| `api-service/frontend/` | Existing testing UI source, with its own package/lockfile and [laptop instructions](https://github.com/Vishwas2311/meeting-intelligence-platform/blob/api-service-delivery/api-service/frontend/README.md) |
| `api-service/migrations/` | Database schema setup/versioning, including a fresh Oracle database; no SQLite data transfer |
| `api-service/load/` | Performance qualification tools |
| `api-service/examples/` | API consumer examples |
| `api-service/deploy/` | API-only production deployment |

Clone this branch on your laptop:

```text
git clone --branch api-service-delivery --single-branch https://github.com/Vishwas2311/meeting-intelligence-platform.git
cd meeting-intelligence-platform
```

For local UI use, follow the [testing UI README](https://github.com/Vishwas2311/meeting-intelligence-platform/blob/api-service-delivery/api-service/frontend/README.md). UI files are copied from the existing application; no new UI/API integration or feature changes are introduced. The local UI expects the API on the same laptop. It is not configured to connect directly to a hosted company API.

Production Docker images, Compose services, CI checks and the backend handover ZIP remain API-only. The UI is source for local testing, not an additional production application. Backend deployment does not require Node.js. Use a Git clone or GitHub source download to obtain the optional UI; the backend ZIP deliberately excludes it.

Start with [service setup](api-service/README.md), [production deployment](api-service/deploy/README.md), [API contract](api-service/docs/guides/API_CONTRACT.md), [readiness](api-service/docs/READINESS.md) and [qualification tests](api-service/docs/QUALIFICATION_TEST_PLAN.md).

This is a GO WITH CHANGES integration candidate, not a production-approved release. The last full backend runs passed 241 tests with two Oracle skips on Windows and Linux; the current source still requires real Oracle/load/UAT/security/operations acceptance. No production data, secrets, generated evidence, archives, installed dependencies or design assets are included.
