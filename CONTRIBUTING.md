# Coding standards

Use Ponytail full mode: make the smallest complete change that solves the problem.
Read callers before editing shared code. Preserve authorization, validation, error
handling, accessibility, and recording durability. Keep UI and API behavior stable
unless the task requires a change.

## Source conventions

- Save UTF-8 files with LF endings; `.editorconfig` defines indentation.
- Python uses Ruff formatting and linting, sorted imports, and 100-character
  formatting. Annotate shared functions and validate request/configuration inputs.
- TypeScript uses strict checking, unused-code checks, and Prettier. Use explicit
  shared API types, `unknown` for untrusted values, and narrow errors before access.
  TypeScript response types describe the API contract; backend validation remains
  authoritative.
- Keep helpers next to their callers. Extract shared code when it has real callers;
  avoid adding layers, wrappers, dependencies, or future features without a need.
- Comments explain a reason or limitation. Record deliberate limitations as
  `shortcut: <limit>, <when to upgrade>` when taking a shortcut.
- A database request succeeding is not a committed transaction. Report durable
  audio only after commit; keep pending data until its acknowledgement succeeds.
- Never commit `.env`, credentials, recordings, databases, generated builds, or
  dependency folders. Keep example configuration sanitized.

## Required local checks

From `backend`, with the virtual environment active:

```powershell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

From `frontend`:

```powershell
npm ci
npm run check
npm run build
```

To apply formatting, run `python -m ruff format .` in `backend` and
`npm run format` in `frontend`. Review formatter changes before committing.

Add focused regression tests for changed logic. Use synthetic meeting data; unit
tests must not call Azure or the hosted LLM. Changes to
layout or capture controls also need a browser check at desktop and phone sizes.
GitHub Actions runs the code checks on pushes and pull requests.

## Existing integration boundaries

The MoM HTML preview remains a separate sample-data design. Recording recovery,
production identity, worker leases, and migrations have
remaining integration work documented in `docs/INTEGRATION_CHECKLIST.md`. Passing
code checks does not validate these services or real microphone capture.
