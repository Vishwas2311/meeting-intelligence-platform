# Meeting Intelligence Platform

A React/TypeScript and FastAPI application for recording meetings, viewing live
transcripts, generating LLM minutes, and reviewing, editing, and approving them.

## Documentation

| Guide                                | Contents                                                       |
| ------------------------------------ | -------------------------------------------------------------- |
| [Setup](docs/SETUP.md)               | Environment, startup, Docker, and development checks.          |
| [Architecture](docs/ARCHITECTURE.md) | Implemented data flow, source layout, and current limitations. |

Start with the setup guide. The app uses a frontend, API, and background worker;
local defaults are http://localhost:5173 and http://127.0.0.1:8000.

Backend history retains meetings, transcripts and saved minutes beyond ten records.
The latest ten complete snapshots are additionally cached for read-only offline
viewing while the frontend is running. See the [office update instructions](docs/SETUP.md#updating-the-existing-office-laptop-installation)
to preserve existing data and upgrade job recovery safely.

The live minutes screen uses an editable document format with transcript evidence and immutable approved revisions. Local startup is supervised with `start.cmd` or `backend/.venv/Scripts/python.exe run.py`. Verified full backups and opt-in audio retention are available; automatic audio deletion is off by default.

Keep real credentials in local `.env` files. Sanitized `.env.example` templates
are included; local recordings, databases, and generated files are ignored.
