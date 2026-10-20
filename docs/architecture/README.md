# Application architecture diagram

This diagram documents the existing Meeting Intelligence application, verified on 19 October 2026. It does not describe a deployed Azure VM solution.

- Open `index.html` for a self-contained zoomable viewer and print/PDF export.
- Open `meeting-intelligence-architecture.drawio` with https://app.diagrams.net/ (File → Open From → Device). Boxes, text and connectors are editable; icons are embedded.
- Use the SVG for sharp documentation or the PNG for sharing.

## Verified boundaries

The browser streams audio directly to Azure Speech using a short-lived token obtained through FastAPI. Independently, MediaRecorder audio and finalized text enter durable IndexedDB outboxes before synchronization to the backend. The Vite development proxy attaches the local API credential server-side.

FastAPI persists meeting state, transcript segments, recording manifests, durable jobs, minutes, immutable revisions and audit records through SQLAlchemy to the currently configured SQLite database. Audio bytes are filesystem files; their metadata and checksums are in the database. A single worker claims leased jobs from that same database. There is no external message broker.

The worker uses OpenAI (`gpt-4.1-mini` in the current local configuration) to generate schema-validated, evidence-linked minutes; Azure OpenAI is a supported alternative. Long-meeting section checkpoints survive retry. Recovery checks the saved audio, decodes WebM using FFmpeg and invokes Azure Fast Transcription. The UI polls active jobs every three seconds and displays generated minutes for human review and approval.

The browser caches ten complete meeting snapshots. This does not cap or delete server history. Audio retention is disabled in the current configuration; enabling it applies existing age/safety checks, not whole-meeting deletion. The automatic full-history retention discussed in chat is a proposal and is not represented as implemented.

`compose.yaml` defines PostgreSQL 16, FastAPI, one worker and persistent database/audio volumes. It is an alternative deployment configuration, not the active local SQLite runtime. A production VM, TLS/SSO, Azure Blob Storage, Azure SQL, Key Vault and cloud monitoring are not shown as existing infrastructure. Current development proxy and fixed local-user identity must not be treated as shared-VM authentication.

## Source evidence

| Concern | Source |
| --- | --- |
| Capture, Speech SDK, transcript and MoM interaction | `frontend/src/main.tsx` |
| Durable outboxes and ten-snapshot cache | `frontend/src/offline.ts` |
| Local proxy / backend credential injection | `frontend/vite.config.mjs` |
| Active job polling | `frontend/src/usePolling.ts` |
| API and lifecycle boundaries | `backend/app/main.py` |
| SQLAlchemy entities | `backend/app/models.py` |
| Worker leases, checkpoints and generation | `backend/app/worker.py` |
| Speech and LLM transports | `backend/app/providers.py` |
| Verified recovery and completeness | `backend/app/recovery.py` |
| Backup / restore / opt-in retention | `backend/app/storage.py` |
| Process supervision | `run.py` |
| Alternative PostgreSQL deployment | `compose.yaml` |

Azure Speech and general browser/cache/code/file/process icons are embedded unmodified from [Microsoft's official architecture icon set](https://learn.microsoft.com/en-us/azure/architecture/icons/), used under its architecture/documentation terms. Generic icons represent application-owned components and OpenAI. No credentials or meeting content are included.
