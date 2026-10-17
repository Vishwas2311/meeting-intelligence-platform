# Frontend

React + TypeScript + Vite. The Azure Speech browser SDK runs continuous recognition (`recognizing` interim, `recognized` final) using an authorization token from `POST /api/speech/token`. Source audio is independently recorded to IndexedDB then synced as recoverable chunks to FastAPI.

Set `VITE_API_URL` in `.env` (`http://localhost:8000` by default; use your actual backend port). Install dependencies with `npm install` and start with `npm run dev`.

During local development, Vite reads `APP_API_TOKEN` from `backend/.env` and attaches it to proxied `/api` requests. Open the app directly; no token entry is required. The dev server binds to `127.0.0.1` and rejects API requests from remote clients or other origins. Never put the token in a `VITE_` variable. Restart the backend and frontend after changing the token.

The credential proxy is absent from production builds and `vite preview`. The existing manual-token connection remains available in those builds; production deployments should use SSO. Browser security requires HTTPS or localhost for microphone capture.

Run `npm run check` for formatting, strict TypeScript, and the proxy, offline
persistence, and stopwatch tests. Run `npm run build` for production compilation.
`npm run format` applies the shared formatting rules. See
[Coding standards](../CONTRIBUTING.md) for the complete workflow.

The live workspace keeps recording controls beside the transcript on desktop.
The independent stopwatch updates in tenths and freezes while paused.

Open `http://localhost:5173/mom-preview.html` while the development server is
running to review the proposed minutes layout with fictional sample data.
This is a standalone prototype; its edits and approvals are local simulations.
See [the preview notes](../docs/MOM_UI_PREVIEW.md) for verification and integration
status.

Limitations: microphone is not Teams system audio; tab audio requires browser support; browser shutdown stops capture; unsynced chunks can persist locally but storage quota may be exceeded; support for per-chunk audio transcription is not automatic. Browser service-worker cannot continue microphone capture after browser/tab closes.
