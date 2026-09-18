"""
Configuration via pydantic-settings (Phase 1.2).
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = os.getenv("CORE_DATABASE_URL", "sqlite+aiosqlite:///cargoresq.db")
    # A development fallback keeps the zero-config demo runnable. Production
    # deployments must provide JWT_SECRET through a secret manager.
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-change-this-cargoresq-secret")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")
    environment: str = os.getenv("ENVIRONMENT", "development")
    allowed_origins: str = os.getenv("ALLOWED_ORIGINS", "*")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def allowed_origin_list() -> list[str]:
    """Return configured CORS origins without silently widening production CORS."""
    if settings.allowed_origins.strip() == "*":
        return ["*"]
    return [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]


if settings.environment.lower() == "production" and (
    settings.jwt_secret.startswith("dev-only-") or len(settings.jwt_secret) < 32
):
    raise RuntimeError("JWT_SECRET must be a random 32+ character secret in production")
