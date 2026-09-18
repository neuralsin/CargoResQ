"""
Configuration via pydantic-settings (Phase 1.2).
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = os.getenv("CORE_DATABASE_URL", "sqlite+aiosqlite:///cargoresq.db")
    jwt_secret: str = os.getenv("JWT_SECRET", "cargoresq_super_secret_jwt_key_prod_2026")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
