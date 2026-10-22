"""Small server-side integration example; no keys are embedded or printed."""

import hashlib
import time
from pathlib import Path

import httpx


class MeetingClient:
    def __init__(self, base_url: str, access_token: str):
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": "Bearer " + access_token},
            timeout=30,
            follow_redirects=False,
        )

    def close(self):
        self.http.close()

    def request(self, method, route, payload=None):
        response = self.http.request(method, route, json=payload)
        response.raise_for_status()
        return response.json()

    def create_meeting(self, title, language="auto", source="meeting-audio"):
        # Caller must obtain authorization before choosing this operation.
        return self.request(
            "POST",
            "/api/meetings",
            {
                "title": title,
                "language": language,
                "source": source,
                "consent_confirmed": True,
            },
        )

    def upload_chunk(self, mid, sequence, start_ms, end_ms, content: bytes, mime="audio/webm"):
        response = self.http.post(
            f"/api/meetings/{mid}/audio-chunks",
            data={
                "sequence": str(sequence),
                "start_ms": str(start_ms),
                "end_ms": str(end_ms),
                "sha256": hashlib.sha256(content).hexdigest(),
            },
            files={"audio": (f"{sequence}.webm", content, mime)},
            timeout=120,
        )
        response.raise_for_status()
        return response.json()

    def upload_notes_audio(self, mid, path: Path, mime):
        with path.open("rb") as audio:
            response = self.http.post(
                f"/api/recaps/{mid}/audio",
                data={"transcribe": "true"},
                files={"audio": (path.name, audio, mime)},
                timeout=120,
            )
        response.raise_for_status()
        return response.json()

    def wait_for_job(self, mid, job_id, deadline_seconds=1800):
        end = time.monotonic() + deadline_seconds
        while time.monotonic() < end:
            job = self.request("GET", f"/api/meetings/{mid}/jobs/{job_id}")
            if job["status"] == "completed":
                return job
            if job["status"] == "failed":
                raise RuntimeError(job.get("error") or "Processing failed")
            time.sleep(2)
        raise TimeoutError(
            "Polling deadline reached; inspect job status before retrying generation"
        )

    def edit_minutes(self, mid, revision, data):
        return self.request(
            "PATCH",
            f"/api/meetings/{mid}/mom",
            {"expected_revision": revision, "data": data},
        )

    def approve_minutes(self, mid, revision):
        return self.request(
            "POST",
            f"/api/meetings/{mid}/mom/approve",
            {"approved": True, "expected_revision": revision},
        )
