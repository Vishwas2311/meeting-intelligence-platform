# Meeting Intelligence API service

Backend-only delivery branch. Python/FastAPI API, independent worker and Oracle production persistence. React/testing UI remains unchanged in the original full-stack branch and is excluded here. No Redis is required.

Start with [service setup](api-service/README.md), [production deployment](api-service/deploy/README.md), [API contract](api-service/docs/guides/API_CONTRACT.md), [readiness](api-service/docs/READINESS.md) and [qualification tests](api-service/docs/QUALIFICATION_TEST_PLAN.md).

This is a GO WITH CHANGES integration candidate, not a production-approved release. The last full backend runs passed 241 tests with two Oracle skips on Windows and Linux; the current source still requires real Oracle/load/UAT/security/operations acceptance. No production data, secrets, generated evidence, archives, design assets or UI sources are included.
