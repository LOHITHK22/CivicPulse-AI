"""Typed configuration: environment settings + the YAML ingestion config."""

from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    """Directory holding ``configs/``.

    Order: ``CIVICPULSE_REPO_ROOT``; the source checkout (editable install); the current
    directory. A regular (non-editable) install puts this file in site-packages, where
    ``parents[3]`` is not the project, which is why Docker images set the env variable.
    """
    env = os.environ.get("CIVICPULSE_REPO_ROOT")
    if env:
        return Path(env).resolve()
    checkout = Path(__file__).resolve().parents[3]
    if (checkout / "configs").is_dir():
        return checkout
    return Path.cwd().resolve()


REPO_ROOT = _repo_root()
DEFAULT_INGESTION_CONFIG = REPO_ROOT / "configs" / "ingestion.yaml"


class Settings(BaseSettings):
    """Environment-driven settings. Secrets come only from the environment / .env."""

    model_config = SettingsConfigDict(
        env_prefix="CIVICPULSE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    data_dir: Path = REPO_ROOT / "data"
    socrata_app_token: str | None = None
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    api_rate_limit_per_minute: int = Field(default=600, ge=1, le=100_000)
    # Number of trusted reverse proxies that APPEND to X-Forwarded-For in front of the API.
    # 0 (default) = ignore X-Forwarded-For entirely (it is client-controlled and spoofable).
    # The Next.js rewrite proxy does NOT append to X-Forwarded-For (verified 2026-11-03), so it
    # does not count as a hop; only an edge load balancer/CDN that appends the peer IP does.
    api_trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    log_level: str = "INFO"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


class SourceConfig(BaseModel):
    dataset_id: str = Field(pattern=r"^[a-z0-9]{4}-[a-z0-9]{4}$")
    base_url: str
    fields: list[str]
    system_fields: list[str]

    @property
    def resource_url(self) -> str:
        return f"{self.base_url}/resource/{self.dataset_id}.json"

    @property
    def metadata_url(self) -> str:
        return f"{self.base_url}/api/views/{self.dataset_id}.json"


class CategoryConfig(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str
    complaint_types: list[str] = Field(min_length=1)


class BackfillConfig(BaseModel):
    start: dt.date
    end: dt.date

    @model_validator(mode="after")
    def _ordered(self) -> BackfillConfig:
        if self.end <= self.start:
            raise ValueError("backfill.end must be after backfill.start")
        return self


class HttpConfig(BaseModel):
    page_size: int = Field(ge=1, le=50_000)
    timeout_seconds: float = Field(gt=0, le=600)
    max_retries: int = Field(ge=0, le=20)
    backoff_base_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)
    max_pages_per_run: int = Field(ge=1)


class IncrementalConfig(BaseModel):
    lookback_hours: int = Field(ge=0, le=24 * 30)


class IngestionConfig(BaseModel):
    source: SourceConfig
    categories: list[CategoryConfig] = Field(min_length=1)
    backfill: BackfillConfig
    http: HttpConfig
    incremental: IncrementalConfig

    @field_validator("categories")
    @classmethod
    def _unique_keys(cls, v: list[CategoryConfig]) -> list[CategoryConfig]:
        keys = [c.key for c in v]
        if len(keys) != len(set(keys)):
            raise ValueError("category keys must be unique")
        return v

    @property
    def complaint_types(self) -> list[str]:
        return [t for c in self.categories for t in c.complaint_types]

    def category_for(self, complaint_type: str) -> CategoryConfig | None:
        for c in self.categories:
            if complaint_type in c.complaint_types:
                return c
        return None


def load_ingestion_config(path: Path | None = None) -> IngestionConfig:
    with open(path or DEFAULT_INGESTION_CONFIG, encoding="utf-8") as fh:
        return IngestionConfig.model_validate(yaml.safe_load(fh))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
