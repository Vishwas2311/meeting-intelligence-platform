# Meeting Intelligence - API service handoff

Backend-only package for manager, Infrastructure, DBA and Consumer Integration handoff. Includes the Python/FastAPI API, independent worker, Oracle schema setup, API contract, production configuration and backend tests. **No React UI, Node installation, local data or secrets are included. No Redis is required.**

Start with the [manager/team handoff](HANDOFF.md), [service setup](api-service/README.md), [production deployment](api-service/deploy/README.md) and [qualification plan](api-service/docs/QUALIFICATION_TEST_PLAN.md).

| Folder | Purpose |
| --- | --- |
| `api-service/app/` | API and background worker runtime |
| `api-service/migrations/` | Database schema setup/versioning, including a fresh Oracle database; no SQLite data transfer |
| `api-service/load/` | Performance qualification tools |
| `api-service/examples/` | API consumer examples |
| `api-service/deploy/` | API-only production deployment |

Clone this branch on your laptop:

```text
git clone --branch handoff-api-service-package --single-branch https://github.com/Vishwas2311/meeting-intelligence-platform.git meeting-intelligence-api-handoff
cd meeting-intelligence-api-handoff
```

Alternatively use GitHub **Code > Download ZIP** on this branch. GitHub's source ZIP contains the reviewed source tree. The separately built `handoff-api-service-package.zip` adds source-commit metadata and per-file checksums, with a `.sha256` file for the whole ZIP. Both exclude the UI.

Production needs an application host, Oracle, persistent audio storage, HTTPS, protected secrets, approved identities/providers, monitoring and recovery. A Linux physical server or VM can use the supplied Docker recipe; another container platform requires validated deployment configuration. This source package does not provision infrastructure or deploy automatically. The original UI remains on `api-service-delivery`, which stays the repository default.

Start with [service setup](api-service/README.md), [production deployment](api-service/deploy/README.md), [API contract](api-service/docs/guides/API_CONTRACT.md), [readiness](api-service/docs/READINESS.md) and [qualification tests](api-service/docs/QUALIFICATION_TEST_PLAN.md).

**82/100: GO WITH CHANGES for integration handoff, not production approved.** Actual Oracle, load, identities/providers, security, monitoring and restore still require environment acceptance. CI success does not close those gates. No production data, secrets, installed dependencies, diagrams, archived reports or UI source are included.
