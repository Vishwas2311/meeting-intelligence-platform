# Frontend

React + TypeScript + Vite. The Azure Speech browser SDK runs continuous recognition (`recognizing` interim, `recognized` final) using an authorization token from `POST /api/speech/token`. Source audio is independently recorded to IndexedDB then synced as recoverable chunks to FastAPI.

Set `VITE_API_URL` in `.env` (`http://localhost:8000` for local development); install dependencies with `npm install` and start with `npm run dev`. Browser security requires HTTPS or localhost for microphone capture.

Limitations: microphone is not Teams system audio; tab audio requires browser support; browser shutdown stops capture; unsynced chunks can persist locally but storage quota may be exceeded; support for per-chunk audio transcription is not automatic. Browser service-worker cannot continue microphone capture after browser/tab closes.
