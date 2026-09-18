"""
Unified Database Engine & Declarative Base.
Enables instant, zero-configuration local hosting via SQLite async,
with automatic PostgreSQL/PostGIS support when DATABASE_URL is configured.
"""
import os
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from shared.observability import logger


class Base(DeclarativeBase):
    pass


# Import models to ensure they register on Base.metadata
from services.core_api.app.models import Company, Truck, Shipment
from services.escrow_ledger.app.models import Escrow, LedgerEntry
from services.orchestrator.app.models import Incident, IncidentEvent
from shared.idempotency import ProcessedEvent


raw_db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///cargoresq.db")

# Normalize postgres:// or postgresql:// to postgresql+asyncpg:// for cloud hosts (Render, Railway, Fly, Heroku)
if raw_db_url.startswith("postgres://"):
    db_url = raw_db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif raw_db_url.startswith("postgresql://") and "+asyncpg" not in raw_db_url:
    db_url = raw_db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    db_url = raw_db_url

logger.info("database_configured", url_type="postgresql" if "postgres" in db_url else "sqlite")

engine = create_async_engine(db_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_database():
    """Initializes all tables and schemas on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_tables_initialized")
