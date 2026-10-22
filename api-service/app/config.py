import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError
from sqlalchemy.engine import URL, make_url

BACKEND_DIR = Path(__file__).resolve().parents[1]
LOCAL_DATA = BACKEND_DIR / "var"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore"
    )
    app_name: str = "Meeting Intelligence API Service"
    app_env: Literal["development", "test", "production"] = "development"
    public_origin: str = ""
    storage_mount_marker: str = ".meeting-volume"
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)
    db_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_statement_timeout_seconds: int = Field(default=30, ge=1, le=600)
    db_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    shutdown_grace_seconds: int = Field(default=210, ge=1, le=900)
    max_json_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=8 * 1024**2)
    actor_requests_per_minute: int = Field(default=600, ge=1)
    actor_uploads_per_minute: int = Field(default=60, ge=1)
    actor_speech_tokens_per_minute: int = Field(default=12, ge=1)
    actor_jobs_per_hour: int = Field(default=10, ge=1)
    actor_storage_bytes: int = Field(default=2 * 1024**3, ge=1024)
    actor_pending_jobs: int = Field(default=2, ge=1, le=100)
    provider_max_concurrency: int = Field(default=1, ge=1, le=100)
    max_meeting_ms: int = Field(default=7200000, ge=1000, le=7200000)
    max_meeting_audio_bytes: int = Field(default=250 * 1024**2, ge=1024, le=250 * 1024**2)
    max_decoded_audio_bytes: int = Field(default=240 * 1024**2, ge=1024, le=240 * 1024**2)
    worker_id: str = Field(default="", max_length=64)
    job_deadline_seconds: int = Field(default=1800, ge=60, le=14400)
    local_cache_kek: str = Field(default="", repr=False)
    browser_recovery_enabled: bool = True
    local_cache_kek_id: str = "v1"
    local_cache_old_keks: dict[str, str] = Field(default_factory=dict, repr=False)
    policy_approved: bool = False
    usage_policy_version: str = ""
    retention_policy_version: str = ""
    daily_llm_calls: int = Field(default=100, ge=1)
    daily_transcription_calls: int = Field(default=30, ge=1)
    max_reserved_upload_bytes: int = Field(default=500 * 1024**2, ge=1024)
    llm_max_output_tokens: int = Field(default=4000, ge=100, le=32000)
    database_url: str = Field(
        default=f"sqlite:///{LOCAL_DATA / 'meeting_intelligence.db'}", repr=False
    )
    # DATABASE_URL remains the selector; Oracle credentials may be supplied separately.
    oracle_user: str = ""
    oracle_password: str = Field(default="", repr=False)
    oracle_dsn: str = ""
    oracle_config_dir: str = ""
    oracle_wallet_location: str = ""
    oracle_wallet_password: str = Field(default="", repr=False)
    storage_dir: Path = LOCAL_DATA / "audio"
    cors_origins: str = ""
    auth_mode: Literal["api_key", "jwt"] = "api_key"
    api_client_keys: dict[str, str] = Field(default_factory=dict, repr=False)
    api_admin_clients: list[str] = Field(default_factory=list)
    jwt_issuer: str = ""
    jwt_audience: str = ""
    jwt_jwks_url: str = ""
    jwt_required_permission: str = "Meeting.Access"
    jwt_admin_role: str = "Meeting.Admin"
    app_api_token: str = Field(default="", repr=False)
    azure_speech_region: str = ""
    azure_speech_key: str = Field(default="", repr=False)
    azure_speech_fast_endpoint: str = ""
    llm_endpoint: str = ""
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_max_input_chars: int = Field(default=30000, ge=1000)
    llm_response_format: Literal["json_schema", "json_object", "text"] = "json_schema"
    llm_request_interval_seconds: float = Field(default=2, ge=0, le=60)
    audio_retention_days: int = Field(default=30, gt=0)
    audio_retention_enabled: bool = False
    max_audio_chunk_mb: int = Field(default=16, gt=0, le=16)
    max_recap_audio_mb: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="before")
    @classmethod
    def secret_files(cls, raw):
        values = dict(raw)
        for name in (
            "oracle_password",
            "oracle_wallet_password",
            "app_api_token",
            "api_client_keys",
            "azure_speech_key",
            "llm_api_key",
            "local_cache_kek",
            "local_cache_old_keks",
        ):
            filename = values.pop(name + "_file", None) or os.getenv(name.upper() + "_FILE")
            if not filename:
                continue
            if values.get(name):
                raise ValueError(f"Configure only {name.upper()} or its _FILE setting")
            try:
                secret = Path(filename).read_text(encoding="utf-8").strip()
                if not secret:
                    raise ValueError
                values[name] = (
                    json.loads(secret)
                    if name in {"api_client_keys", "local_cache_old_keks"}
                    else secret
                )
            except (OSError, ValueError):
                raise ValueError(f"Cannot load {name.upper()} secret file") from None
        return values

    @model_validator(mode="after")
    def stable_storage_paths(self):
        if self.app_env == "production":
            if self.auth_mode != "jwt" or self.app_api_token or self.api_client_keys:
                raise ValueError(
                    "Production requires company JWT authentication without local/service keys"
                )
            if (
                not self.public_origin.startswith("https://")
                or not self.policy_approved
                or not self.usage_policy_version
            ):
                raise ValueError(
                    "Production requires an HTTPS public origin and approved usage policy"
                )
            if self.browser_recovery_enabled and not self.local_cache_kek:
                raise ValueError("Production requires a browser-cache wrapping key")
            import base64

            try:
                if (
                    self.browser_recovery_enabled
                    and len(base64.b64decode(self.local_cache_kek, validate=True)) != 32
                ):
                    raise ValueError
            except ValueError:
                raise ValueError(
                    "Browser-cache wrapping key must be base64 encoded 32 bytes"
                ) from None
            for endpoint in (self.llm_endpoint, self.azure_speech_fast_endpoint):
                if not endpoint.startswith("https://"):
                    raise ValueError("Production requires configured HTTPS provider endpoints")
            if not all(
                [self.azure_speech_region, self.azure_speech_key, self.llm_api_key, self.llm_model]
            ):
                raise ValueError("Production requires configured Speech and LLM credentials/model")
            if (
                "*" in self.cors_origins
                or self.audio_retention_enabled
                and not self.retention_policy_version
            ):
                raise ValueError("Production CORS/retention policy is not safe")
        if self.auth_mode == "jwt":
            if not self.jwt_issuer.startswith("https://") or not self.jwt_audience:
                raise ValueError("JWT mode requires an HTTPS issuer and an API audience")
            if not self.jwt_jwks_url.startswith("https://") or not self.jwt_required_permission:
                raise ValueError("JWT mode requires an HTTPS JWKS URL and required permission")
        if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", client) for client in self.api_client_keys):
            raise ValueError("Client IDs must be 1-64 letters, numbers, underscores or hyphens")
        if any(
            not key.isascii() or not 32 <= len(key) <= 256 for key in self.api_client_keys.values()
        ):
            raise ValueError("Service API keys must contain 32-256 ASCII characters")
        if len(set(self.api_client_keys.values())) != len(self.api_client_keys):
            raise ValueError("Each client must have a unique API key")
        if self.database_url == "oracle":
            if not all([self.oracle_user, self.oracle_password, self.oracle_dsn]):
                raise ValueError("Oracle requires ORACLE_USER, ORACLE_PASSWORD and ORACLE_DSN")
            self.database_url = URL.create(
                "oracle+oracledb",
                username=self.oracle_user,
                password=self.oracle_password,
                query={"dsn": self.oracle_dsn},
            ).render_as_string(hide_password=False)
        elif make_url(self.database_url).get_backend_name() == "oracle":
            if make_url(self.database_url).drivername != "oracle+oracledb":
                raise ValueError("Use oracle+oracledb or DATABASE_URL=oracle")
        # Launching from a different terminal directory must not create a second history database.
        url = make_url(self.database_url)
        if self.app_env == "production" and url.get_backend_name() != "oracle":
            raise ValueError("Production database must be the approved Oracle service")
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
    try:
        return Settings()
    except ValidationError as error:
        fields = sorted(
            {str(item["loc"][0]) for item in error.errors(include_input=False) if item["loc"]}
        )
        raise RuntimeError(
            "Invalid application configuration" + (": " + ", ".join(fields) if fields else "")
        ) from None
    except SettingsError:
        raise RuntimeError("Invalid application configuration format") from None
