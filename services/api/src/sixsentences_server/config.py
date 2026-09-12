"""Deployment-owned settings with no hosted-service defaults."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded exclusively from the local environment."""

    model_config = SettingsConfigDict(
        env_prefix="SIX_COMMUNITY_",
        env_file=None,
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite:///./var/community.sqlite3"
    auto_create_schema: bool = False
    registration_enabled: bool = True
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    session_hours: int = Field(default=168, ge=1, le=24 * 90)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=250 * 1024 * 1024)
    worker_poll_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    worker_max_attempts: int = Field(default=3, ge=1, le=20)

    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_starttls: bool = True
    smtp_username: str = ""
    smtp_password: SecretStr = Field(default_factory=lambda: SecretStr(""))
    smtp_from: str = "community@example.invalid"

    ai_provider: Literal["disabled", "openai_compatible"] = "disabled"
    ai_base_url: str = ""
    ai_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    ai_model: str = ""
    ai_timeout_seconds: float = Field(default=45.0, ge=1.0, le=300.0)
    ai_max_output_tokens: int = Field(default=1200, ge=64, le=16000)

    voice_provider: Literal["disabled", "openai_compatible"] = "disabled"
    voice_base_url: str = ""
    voice_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    voice_model: str = ""
    voice_timeout_seconds: float = Field(default=45.0, ge=1.0, le=180.0)
    public_session_limit: int = Field(default=8, ge=1, le=1000)
    public_audio_chunk_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)

    openalex_mailto: str = ""
    openalex_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    research_result_limit: int = Field(default=100, ge=1, le=1000)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Accept only the database families covered by migrations and tests."""

        if not value.startswith(("sqlite:", "postgresql+psycopg:")):
            raise ValueError("database_url must use sqlite or postgresql+psycopg")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        """Reject wildcard and credential-bearing browser origins."""

        clean: list[str] = []
        for value in values:
            parsed = urlsplit(value)
            if (
                value == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError(f"unsafe allowed origin: {value!r}")
            clean.append(value.rstrip("/"))
        return clean

    @field_validator("allowed_hosts")
    @classmethod
    def validate_hosts(cls, values: list[str]) -> list[str]:
        """Accept hostnames only; schemes, paths and credential syntax are invalid."""

        clean: list[str] = []
        for value in values:
            host = value.strip().casefold()
            if not host or "://" in host or "/" in host or "@" in host or " " in host:
                raise ValueError(f"unsafe allowed host: {value!r}")
            clean.append(host)
        return clean

    @model_validator(mode="after")
    def validate_provider_settings(self) -> Settings:
        """Require explicit endpoints and models before content can leave the host."""

        for name, provider, base_url, model in (
            ("ai", self.ai_provider, self.ai_base_url, self.ai_model),
            ("voice", self.voice_provider, self.voice_base_url, self.voice_model),
        ):
            if provider == "disabled":
                continue
            parsed = urlsplit(base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or not model.strip()
            ):
                raise ValueError(f"{name} provider needs a safe base URL and model")
            if self.environment == "production" and parsed.scheme != "https":
                raise ValueError(f"{name} provider must use HTTPS in production")
        if self.environment == "production" and self.auto_create_schema:
            raise ValueError("run migrations explicitly in production")
        if self.environment == "production" and "*" in self.allowed_hosts:
            raise ValueError("wildcard allowed_hosts is not permitted in production")
        return self

    @property
    def sqlite_path(self) -> Path | None:
        """Return the configured SQLite path when it names a file."""

        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            return None
        value = self.database_url.removeprefix(prefix)
        return None if value == ":memory:" else Path(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings object."""

    return Settings()
