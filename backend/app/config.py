import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_DATA = (
    Path(os.getenv("LOCALAPPDATA") or os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "MeetingIntelligence"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore"
    )
    app_name: str = "Meeting Intelligence API"
    database_url: str = f"sqlite:///{LOCAL_DATA / 'meeting_intelligence.db'}"
    storage_dir: Path = LOCAL_DATA / "audio"
    cors_origins: str = "http://localhost:5173"
    app_api_token: str = ""
    azure_speech_region: str = ""
    azure_speech_endpoint: str = ""
    azure_speech_key: str = ""
    azure_speech_key_secondary: str = ""
    azure_speech_fast_endpoint: str = ""
    azure_speech_fast_api_version: str = ""
    llm_endpoint: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_api_version: str = ""
    llm_max_input_chars: int = Field(default=30000, ge=1000)
    llm_response_format: Literal["json_schema", "json_object", "text"] = "json_schema"
    llm_request_interval_seconds: float = Field(default=2, ge=0, le=60)
    audio_retention_days: int = Field(default=30, gt=0)
    audio_retention_enabled: bool = False
    max_audio_chunk_mb: int = Field(default=16, gt=0)

    @model_validator(mode="after")
    def stable_storage_paths(self):
        # Launching from a different terminal directory must not create a second history database.
        url = make_url(self.database_url)
        if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
            path = Path(url.database).expanduser()
            if not path.is_absolute():
                url = url.set(database=str(BACKEND_DIR / path))
                self.database_url = url.render_as_string(hide_password=False)
        if not self.storage_dir.is_absolute():
            self.storage_dir = BACKEND_DIR / self.storage_dir
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
