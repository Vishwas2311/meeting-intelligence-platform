"""Print local setup facts without keys, endpoint URLs, transcript text or meeting titles."""

import json
import shutil
import sys
from importlib.metadata import version
from pathlib import Path

from sqlalchemy import func, inspect, select
from sqlalchemy.engine import make_url

from .config import get_settings
from .database import SessionLocal, engine
from .models import Job, Meeting


def report() -> dict:
    settings = get_settings()
    url = make_url(settings.database_url)
    info = {
        "python": sys.version.split()[0],
        "packages": {
            name: version(name) for name in ("fastapi", "sqlalchemy", "httpx", "pydantic")
        },
        "database_kind": url.get_backend_name(),
        "api_token_configured": bool(settings.app_api_token),
        "speech_configured": bool(settings.azure_speech_region and settings.azure_speech_key),
        "llm_configured": bool(
            settings.llm_endpoint and settings.llm_api_key and settings.llm_model
        ),
        "llm_max_input_chars": settings.llm_max_input_chars,
        "llm_response_format": settings.llm_response_format,
        "storage_exists": settings.storage_dir.is_dir(),
        "disk_free_mb": shutil.disk_usage(
            next(
                path
                for path in [settings.storage_dir, *settings.storage_dir.parents]
                if path.exists()
            )
        ).free
        // 1048576,
    }
    # Do not create a missing DB or probe a company/network database as a diagnostic side effect.
    if url.get_backend_name() == "sqlite" and url.database and Path(url.database).is_file():
        try:
            columns = {item["name"] for item in inspect(engine).get_columns("jobs")}
            info["job_recovery_schema_ready"] = {
                "run_after",
                "lease_until",
                "claim_token",
                "checkpoint",
            } <= columns
            with SessionLocal() as db:
                info["saved_meetings"] = db.scalar(select(func.count()).select_from(Meeting))
                info["job_counts"] = dict(
                    db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
                )
        except Exception as error:
            info["database_error_type"] = type(error).__name__
    else:
        info["database_probed"] = False
    return info


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
