from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Any SQLAlchemy URL works, e.g. postgresql+psycopg://user:pass@host/db
    database_url: str = "sqlite:///./data/certificates.db"
    storage_dir: Path = Path("./data/certificates")
    max_recipients_per_job: int = 1000
    worker_threads: int = 4
    # "background": jobs run on a thread pool after the HTTP response is sent.
    # "inline": jobs run before the response returns (used by the test-suite).
    processing_mode: Literal["background", "inline"] = "background"
    recover_jobs_on_startup: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
