"""
Configuration via pydantic-settings (Phase 1.2).

This module is the single source of truth for runtime configuration. Nothing
else in the codebase may read os.getenv for a value that appears here -- a
second reader is how the JWT secret and the database URL previously diverged
between the signing path and the verifying path.
"""
import os
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


def _database_url_default() -> str:
    """Resolve the database URL from the documented environment variable.

    DATABASE_URL is what every document, docker-compose file and deployment
    runbook instructs operators to set. CORE_DATABASE_URL is accepted as a
    deprecated alias so existing local setups keep working.
    """
    return (
        os.getenv("DATABASE_URL")
        or os.getenv("CORE_DATABASE_URL")
        or "sqlite+aiosqlite:///cargoresq.db"
    )


class Settings(BaseSettings):
    database_url: str = _database_url_default()
    # A development fallback keeps the zero-config demo runnable. Production
    # deployments must provide JWT_SECRET through a secret manager.
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-change-this-cargoresq-secret")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120

    #: How long a driver can stay signed in without re-entering a
    #: password. Matched to a working roster rather than a session:
    #: the phone is the driver's own, held by the person it belongs
    #: to, and signing them out mid-shift is the worse risk.
    refresh_token_expire_days: int = 30
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
    environment: str = os.getenv("ENVIRONMENT", "development")
    allowed_origins: str = os.getenv("ALLOWED_ORIGINS", "*")
    osrm_base_url: str = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org")

    # Schema management. Alembic is the only schema authority; the app never
    # calls create_all. Development boots run the upgrade automatically so
    # `python main.py` still just works.
    run_migrations_on_boot: bool = os.getenv("RUN_MIGRATIONS_ON_BOOT", "").lower() in {
        "1",
        "true",
        "yes",
    } or os.getenv("ENVIRONMENT", "development").lower() != "production"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def allowed_origin_list() -> list[str]:
    """Return configured CORS origins without silently widening production CORS."""
    if settings.allowed_origins.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]


def is_production() -> bool:
    return settings.environment.lower() == "production"


if is_production() and (
    settings.jwt_secret.startswith("dev-only-") or len(settings.jwt_secret) < 32
):
    raise RuntimeError("JWT_SECRET must be a random 32+ character secret in production")
