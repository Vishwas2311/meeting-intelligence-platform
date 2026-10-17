from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8-sig", extra="ignore"
    )
    app_name: str = "Meeting Intelligence API"
    database_url: str = "sqlite:///./meeting_intelligence.db"
    storage_dir: Path = Path("./data")
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
    llm_max_input_chars: int = Field(default=100000, gt=0)
    audio_retention_days: int = Field(default=30, gt=0)
    max_audio_chunk_mb: int = Field(default=16, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
