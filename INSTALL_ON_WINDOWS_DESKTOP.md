# Install on Windows Desktop

1. Download and unzip `meeting-intelligence-platform.zip`.
2. Move the extracted `meeting-intelligence-platform` folder to your Windows Desktop.
3. Open PowerShell in `Desktop\meeting-intelligence-platform\backend`.
4. Run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

In the `.env` file, set `APP_API_TOKEN` to a long random password and set your Speech region/key, and LLM endpoint/key/model. Keep your Azure Speech **Fast Transcription** URL separately in `AZURE_SPEECH_FAST_ENDPOINT`; it is not the live SDK streaming URL. Do not put subscription keys in browser code.

5. Start API:

```powershell
uvicorn app.main:app --reload
```

6. In a second PowerShell terminal, with the same virtual environment activated, run:

```powershell
python -m app.worker
```

7. Open http://127.0.0.1:8000/docs. Use `Authorization: Bearer <APP_API_TOKEN>`.
8. Install test tools with `python -m pip install -r requirements-dev.txt`, then
   test the backend with `python -m pytest -q`.

9. In a third terminal, open `frontend`, run `npm ci`, and then `npm run dev`.
   Configure `frontend/.env` from its example first. Open http://localhost:5173.

**Note:** This is an integration build. Browser microphone capture does not
automatically capture every Teams desktop participant. Live Speech connectivity,
recovery, and production integrations still need validation. Read README.md,
CONTRIBUTING.md, and docs/INTEGRATION_CHECKLIST.md before deployment.
