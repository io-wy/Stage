from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import AnyUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Use a .env file for local development.
    """

    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_prefix="APP_",
        case_sensitive=False,
        extra="ignore",
    )

    # Core
    ENV: str = "development"
    PROJECT_NAME: str = "openagents-orchestration-api"
    API_V1_STR: str = "/api/v1"

    # CORS
    CORS_ORIGINS: List[str] = ["*"]

    # Database
    DATABASE_URL: str = "sqlite:///./app.db"
    SQLITE_CONNECT_ARGS_CHECK_SAME_THREAD: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
