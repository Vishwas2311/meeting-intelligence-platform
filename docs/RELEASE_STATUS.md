# Verification record (2026-10-16)

## Verified locally

- Backend: `python -m pytest -q` => **5 passed**. Test fixtures disable Azure Speech credentials, including when a local `.env` contains real keys.
- Frontend: `npm run build` => TypeScript and Vite production build passed. Vite reports a nonblocking bundle-size warning.
- Running UI and authenticated API/database readiness checks passed.
- Azure OpenAI: the configured `gpt-6.1-sol` deployment generated minutes from a synthetic transcript; the application validated the response schema.
- Chrome UI checks covered empty/invalid/valid tokens, connection persistence, recording consent, history, transcript display, summary generation, invalid JSON, draft saving, approval, empty-transcript failure, and new-meeting reset.
- Desktop, 390 px mobile, and 768 px tablet layouts were checked. Browser console inspection reported no warnings or JavaScript errors in the final test tab.

## Automatic local environment authentication

- Local `npm run dev` reads `APP_API_TOKEN` from `backend/.env` on the Vite server and forwards authenticated `/api` requests. No token entry is required in the local UI.
- The proxy only accepts local clients and matching browser origins. Direct backend requests still require a valid token.
- Six `npm test` checks pass for the proxy access rules, server-only credential handling, invalid configuration, and production/preview isolation.
- Live checks passed for localhost and 127.0.0.1, HTTP 403 on cross-origin requests, and HTTP 401 on unauthenticated direct backend requests.
- No configured credential was found in the served API module or production assets. The backend `.env` file was inaccessible through Vite.
- A fresh Chrome tab loaded meeting history and passed Test Backend without entering a token. This automatic flow is restricted to development; production builds keep the existing authentication flow.

## Fixes included

- Connection saving validates the token before storing it. Editing the token does not make authentication requests on every change, and successful saves clear old errors.
- Draft saves clear stale errors. Unsaved edits must be saved before approval, and approved minutes are read-only.
- The API rejects regeneration of approved minutes; a regression test verifies that their saved content remains unchanged.
- New Meeting clears the remembered selection and resets transcript, consent, statuses, jobs, and messages.
- Pending/failed summary jobs have appropriate messages; completed jobs clear outdated queue notices.
- Tablet sidebar brand text stays inside the sidebar.

## Not verified or blocked

- Synthetic data only was used. Microphone/tab capture, real recording pause/resume, audio chunk timing, offline recording, and reconnection were not exercised.
- Azure Speech authentication remains unresolved; no successful live Speech streaming test has been completed.
- The internal form integration remains disabled. Its confirmation was checked and the API rejected submission with HTTP 503; the real destination was not contacted.
- Browser automation stalled after cancelling the native submission dialog. Testing continued in a fresh tab; the API confirmed that no submission was queued.
- Fully automatic audio gap recovery and all-participant Teams desktop capture are not implemented. The project remains an integration build.

## Before production release

Validate a long meeting with valid Speech credentials and controlled network outages. Check transcript timestamps after reconnect and local audio reassembly/transcoding before Fast Transcription recovery. Add an approved desktop capture integration if Teams desktop audio is required. Complete SSO, managed storage, schema migrations, durable job scheduling, secret management, backup, observability, the approved internal form mapping, and a security review.
