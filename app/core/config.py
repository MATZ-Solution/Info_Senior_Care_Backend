"""
Centralized application configuration.

All environment-dependent values are loaded here ONCE and reused everywhere.
Never read os.environ directly elsewhere in the codebase — always import
`settings` from this module. This keeps config validated, typed, and testable.
"""
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---- Environment ----
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ---- Supabase ----
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str

    # ---- Database ----
    DATABASE_URL: str
    MIGRATION_DATABASE_URL: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10

    # ---- Cache (Valkey - Redis protocol compatible) ----
    VALKEY_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_SECONDS: int = 180

    # ---- CORS ----
    CORS_ORIGINS: List[str] = ["http://localhost:8081"]

    # ---- Rate limiting ----
    RATE_LIMIT_PER_MINUTE: int = 60

    # ---- Monitoring ----
    SENTRY_DSN: str = ""

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}, got {v!r}")
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — env is parsed only once per process."""
    return Settings()


settings = get_settings()
