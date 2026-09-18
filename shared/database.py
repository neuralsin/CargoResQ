"""
Unified database engine and declarative base.

Runs on SQLite for local demos and tests, and on PostgreSQL/PostGIS when
DATABASE_URL points at one. The schema itself is owned by Alembic -- this
module never calls create_all, because running both meant the schema you got
depended on how the database happened to be provisioned.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from services.core_api.app.config import settings
from shared.observability import logger


class Base(DeclarativeBase):
    pass


# NOTE: model modules are deliberately NOT imported here.
#
# Every model module imports Base from this module, so importing them back
# creates a cycle that only resolves when shared.database happens to be
# imported first. Registration on Base.metadata now lives in
# shared.models_registry, which the app entrypoint and Alembic import
# explicitly.


def normalise_database_url(raw: str) -> str:
    """Normalise a managed-host URL to an async driver.

    Render, Railway, Fly and Heroku all hand out `postgres://` or
    `postgresql://` URLs, neither of which SQLAlchemy's async engine accepts.
    """
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql://") and "+asyncpg" not in raw:
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


db_url = normalise_database_url(settings.database_url)
IS_SQLITE = db_url.startswith("sqlite")

logger.info("database_configured", url_type="postgresql" if not IS_SQLITE else "sqlite")

engine_kwargs = {"echo": False}
# SQLite's aiosqlite worker thread is non-daemon on Windows. A null pool
# closes every connection as soon as its request/session ends, which keeps
# CLI tests and packaged desktop tooling from hanging after successful work.
if IS_SQLITE:
    engine_kwargs["poolclass"] = NullPool
engine = create_async_engine(db_url, **engine_kwargs)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
