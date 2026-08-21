# """
# Centralized application configuration.

# All environment-dependent values are loaded here ONCE and reused everywhere.
# Never read os.environ directly elsewhere in the codebase — always import
# `settings` from this module. This keeps config validated, typed, and testable.
# """
# from functools import lru_cache
# from typing import List

# from pydantic import field_validator
# from pydantic_settings import BaseSettings, SettingsConfigDict


# class Settings(BaseSettings):
#     model_config = SettingsConfigDict(
#         env_file=".env",
#         env_file_encoding="utf-8",
#         case_sensitive=True,
#         extra="ignore",
#     )

#     # ---- Environment ----
#     ENVIRONMENT: str = "development"
#     LOG_LEVEL: str = "INFO"

#     # ---- Supabase ----
#     SUPABASE_URL: str
#     SUPABASE_ANON_KEY: str
#     SUPABASE_SERVICE_ROLE_KEY: str
#     SUPABASE_JWT_SECRET: str

#     # ---- Database ----
#     DATABASE_URL: str
#     ASYNC_DATABASE_URL: str
#     MIGRATION_DATABASE_URL: str
#     DB_POOL_SIZE: int = 10
#     DB_MAX_OVERFLOW: int = 10

#     # ---- Cache (Valkey - Redis protocol compatible) ----
#     VALKEY_URL: str = "redis://localhost:6379/0"
#     CACHE_TTL_SECONDS: int = 180

#     # ---- CORS ----
#     CORS_ORIGINS: List[str] = ["http://localhost:8081"]

#     # ---- Rate limiting ----
#     RATE_LIMIT_PER_MINUTE: int = 60

#     # ---- Monitoring ----
#     SENTRY_DSN: str = ""

#     @field_validator("ENVIRONMENT")
#     @classmethod
#     def validate_environment(cls, v: str) -> str:
#         allowed = {"development", "staging", "production"}
#         if v not in allowed:
#             raise ValueError(f"ENVIRONMENT must be one of {allowed}, got {v!r}")
#         return v

#     @property
#     def is_production(self) -> bool:
#         return self.ENVIRONMENT == "production"


# @lru_cache
# def get_settings() -> Settings:
#     """Cached settings instance — env is parsed only once per process."""
#     return Settings()


# settings = get_settings()


# """
# Centralized application configuration.

# All environment-dependent values are loaded here ONCE and reused everywhere.
# Never read os.environ directly elsewhere in the codebase — always import
# `settings` from this module. This keeps config validated, typed, and testable.
# """
# from functools import lru_cache
# from typing import List

# from pydantic import field_validator
# from pydantic_settings import BaseSettings, SettingsConfigDict


# class Settings(BaseSettings):
#     model_config = SettingsConfigDict(
#         env_file=".env",
#         env_file_encoding="utf-8",
#         case_sensitive=True,
#         extra="ignore",
#     )

#     # ---- Environment ----
#     ENVIRONMENT: str = "development"
#     LOG_LEVEL: str = "INFO"

#     # ---- Supabase ----
#     SUPABASE_URL: str
#     SUPABASE_ANON_KEY: str
#     SUPABASE_SERVICE_ROLE_KEY: str
#     SUPABASE_JWT_SECRET: str

#     # ---- Database ----
#     DATABASE_URL: str
#     ASYNC_DATABASE_URL: str
#     MIGRATION_DATABASE_URL: str
#     DB_POOL_SIZE: int = 10
#     DB_MAX_OVERFLOW: int = 10

#     # ---- Cache (Valkey - Redis protocol compatible) ----
#     VALKEY_URL: str = "redis://localhost:6379/0"
#     CACHE_TTL_SECONDS: int = 180

#     # ---- CORS ----
#     CORS_ORIGINS: List[str] = ["http://localhost:8081"]

#     # ---- Rate limiting ----
#     RATE_LIMIT_PER_MINUTE: int = 60

#     # ---- Monitoring ----
#     SENTRY_DSN: str = ""

#     # ---- TypeSense (search engine only — Postgres stays source of truth) ----
#     # Empty TYPESENSE_HOST/TYPESENSE_API_KEY = integration disabled. The app
#     # must still boot and serve traffic in that state (search falls back to
#     # Postgres), so these are NOT required fields.
#     TYPESENSE_HOST: str = ""
#     TYPESENSE_PORT: int = 443
#     TYPESENSE_PROTOCOL: str = "https"
#     TYPESENSE_API_KEY: str = ""

#     # Operational knobs — sane defaults, overridable per environment.
#     TYPESENSE_COLLECTION: str = "facilities"
#     TYPESENSE_TIMEOUT_SECONDS: float = 5.0
#     TYPESENSE_NUM_RETRIES: int = 3
#     TYPESENSE_RETRY_INTERVAL_SECONDS: float = 1.0

#     # Kill switch: set false to force the Postgres search path even when
#     # TypeSense credentials are present (instant rollback without a redeploy
#     # of code — only an env change + restart).
#     TYPESENSE_SEARCH_ENABLED: bool = True

#     @field_validator("ENVIRONMENT")
#     @classmethod
#     def validate_environment(cls, v: str) -> str:
#         allowed = {"development", "staging", "production"}
#         if v not in allowed:
#             raise ValueError(f"ENVIRONMENT must be one of {allowed}, got {v!r}")
#         return v

#     @field_validator("TYPESENSE_PROTOCOL")
#     @classmethod
#     def validate_typesense_protocol(cls, v: str) -> str:
#         normalized = v.strip().lower()
#         if normalized not in {"http", "https"}:
#             raise ValueError(
#                 f"TYPESENSE_PROTOCOL must be 'http' or 'https', got {v!r}"
#             )
#         return normalized

#     @field_validator("TYPESENSE_HOST")
#     @classmethod
#     def validate_typesense_host(cls, v: str) -> str:
#         """
#         Accept a bare hostname only. A full URL here is the single most common
#         TypeSense Cloud misconfiguration: the client builds `{protocol}://{host}:{port}`
#         itself, so pasting `https://xxx.a1.typesense.net` produces
#         `https://https://xxx...` and every call fails with an opaque connection
#         error at runtime. Fail at startup with a readable message instead.
#         """
#         host = v.strip()
#         if host.startswith(("http://", "https://")):
#             raise ValueError(
#                 "TYPESENSE_HOST must be a bare hostname without a scheme "
#                 "(e.g. 'xxx.a1.typesense.net', not 'https://xxx.a1.typesense.net'). "
#                 "Use TYPESENSE_PROTOCOL for the scheme."
#             )
#         return host.rstrip("/")

#     @field_validator("TYPESENSE_PORT")
#     @classmethod
#     def validate_typesense_port(cls, v: int) -> int:
#         if not 1 <= v <= 65535:
#             raise ValueError(f"TYPESENSE_PORT must be 1-65535, got {v}")
#         return v

#     @property
#     def is_production(self) -> bool:
#         return self.ENVIRONMENT == "production"

#     @property
#     def typesense_configured(self) -> bool:
#         """True only when we have everything needed to reach a TypeSense node."""
#         return bool(self.TYPESENSE_HOST and self.TYPESENSE_API_KEY)


# @lru_cache
# def get_settings() -> Settings:
#     """Cached settings instance — env is parsed only once per process."""
#     return Settings()


# settings = get_settings()


"""
Centralized application configuration.

All environment-dependent values are loaded here ONCE and reused everywhere.
Never read os.environ directly elsewhere in the codebase — always import
`settings` from this module. This keeps config validated, typed, and testable.
"""

from functools import lru_cache
from typing import List
import os
from dotenv import load_dotenv

# Load environment variables from .env file
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(override=True)  # Load environment variables from .env file

CORS_ORIGINS = os.getenv("CORS_ORIGINS", ["http://localhost:8081"])


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
    ASYNC_DATABASE_URL: str
    MIGRATION_DATABASE_URL: str
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 10

    # ---- Cache (Valkey - Redis protocol compatible) ----
    VALKEY_URL: str = "redis://localhost:6379/0"
    CACHE_TTL_SECONDS: int = 180

    # ---- CORS ----
    CORS_ORIGINS: List[str] = CORS_ORIGINS or "http://localhost:8081"

    # ---- Rate limiting ----
    RATE_LIMIT_PER_MINUTE: int = 60

    # ---- Monitoring ----
    SENTRY_DSN: str = ""

    # ---- TypeSense (search engine only — Postgres stays source of truth) ----
    # Empty TYPESENSE_HOST/TYPESENSE_API_KEY = integration disabled. The app
    # must still boot and serve traffic in that state (search falls back to
    # Postgres), so these are NOT required fields.
    TYPESENSE_HOST: str = ""
    TYPESENSE_PORT: int = 443
    TYPESENSE_PROTOCOL: str = "https"

    # The key the RUNNING APP uses. Should be a search-only key: if the
    # backend's environment ever leaks, a search-only key cannot delete the
    # index, drop the collection, or write documents.
    TYPESENSE_API_KEY: str = ""

    # The key the IMPORT SCRIPT uses. Needs admin rights — creating the
    # collection and writing documents are privileged operations a search-only
    # key cannot perform.
    #
    # Kept as a separate variable rather than reusing TYPESENSE_API_KEY so the
    # long-lived web process never holds write credentials it does not need.
    # Only `scripts/typesense_import.py` ever reads this, via
    # `use_admin_credentials()`. If left empty, the import falls back to
    # TYPESENSE_API_KEY (fine when you only have one key, but it will fail on
    # collection creation if that key is search-only).
    TYPESENSE_ADMIN_API_KEY: str = ""

    # Optional: full node list for an HA cluster, copied straight from the
    # TypeSense Cloud dashboard's "Nodes" field. Comma-separated `host:port`.
    #
    #   TYPESENSE_NODES=xyz-1.a1.typesense.net:443,xyz-2.a1.typesense.net:443
    #
    # When set this REPLACES TYPESENSE_HOST/PORT, and the client fails over to
    # a healthy node if one goes down. A single-host config has no failover:
    # that one node restarting takes search down until it returns. Leave empty
    # for a single-node or local Docker setup.
    TYPESENSE_NODES: str = ""

    # Operational knobs — sane defaults, overridable per environment.
    TYPESENSE_COLLECTION: str = "facilities"
    TYPESENSE_TIMEOUT_SECONDS: float = 5.0
    TYPESENSE_NUM_RETRIES: int = 3
    TYPESENSE_RETRY_INTERVAL_SECONDS: float = 1.0

    # Kill switch: set false to force the Postgres search path even when
    # TypeSense credentials are present (instant rollback without a redeploy
    # of code — only an env change + restart).
    TYPESENSE_SEARCH_ENABLED: bool = True

    @field_validator("ENVIRONMENT")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}, got {v!r}")
        return v

    @field_validator("TYPESENSE_PROTOCOL")
    @classmethod
    def validate_typesense_protocol(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in {"http", "https"}:
            raise ValueError(f"TYPESENSE_PROTOCOL must be 'http' or 'https', got {v!r}")
        return normalized

    @field_validator("TYPESENSE_HOST")
    @classmethod
    def validate_typesense_host(cls, v: str) -> str:
        """
        Accept a bare hostname only. A full URL here is the single most common
        TypeSense Cloud misconfiguration: the client builds `{protocol}://{host}:{port}`
        itself, so pasting `https://xxx.a1.typesense.net` produces
        `https://https://xxx...` and every call fails with an opaque connection
        error at runtime. Fail at startup with a readable message instead.
        """
        host = v.strip()
        if host.startswith(("http://", "https://")):
            raise ValueError(
                "TYPESENSE_HOST must be a bare hostname without a scheme "
                "(e.g. 'xxx.a1.typesense.net', not 'https://xxx.a1.typesense.net'). "
                "Use TYPESENSE_PROTOCOL for the scheme."
            )
        return host.rstrip("/")

    @field_validator("TYPESENSE_PORT")
    @classmethod
    def validate_typesense_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError(f"TYPESENSE_PORT must be 1-65535, got {v}")
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def typesense_nodes(self) -> list[dict[str, object]]:
        """
        Node list in the shape the TypeSense client expects.

        Prefers TYPESENSE_NODES (HA cluster) and falls back to the single
        TYPESENSE_HOST. Parsing happens here rather than at the call site so
        the client module never has to know about the string format.

        A node entry may omit the port (`host` instead of `host:443`), in which
        case TYPESENSE_PORT applies — the dashboard sometimes shows one form
        and sometimes the other.
        """
        raw = self.TYPESENSE_NODES.strip()
        if not raw:
            if not self.TYPESENSE_HOST:
                return []
            return [
                {
                    "host": self.TYPESENSE_HOST,
                    "port": self.TYPESENSE_PORT,
                    "protocol": self.TYPESENSE_PROTOCOL,
                }
            ]

        nodes: list[dict[str, object]] = []
        for entry in raw.split(","):
            candidate = entry.strip().rstrip("/")
            if not candidate:
                continue
            # Tolerate a pasted scheme rather than failing — the dashboard
            # value is copied by hand and this is the likeliest slip.
            for scheme in ("https://", "http://"):
                if candidate.startswith(scheme):
                    candidate = candidate[len(scheme) :]
                    break

            host, _, port = candidate.partition(":")
            nodes.append(
                {
                    "host": host,
                    "port": int(port) if port.isdigit() else self.TYPESENSE_PORT,
                    "protocol": self.TYPESENSE_PROTOCOL,
                }
            )
        return nodes

    @property
    def typesense_configured(self) -> bool:
        """True only when we have everything needed to reach a TypeSense node."""
        return bool(self.typesense_nodes and self.TYPESENSE_API_KEY)


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — env is parsed only once per process."""
    return Settings()


settings = get_settings()
