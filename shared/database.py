"""
Unified Database Engine & Declarative Base.
Enables instant, zero-configuration local hosting via SQLite async,
with automatic PostgreSQL/PostGIS support when DATABASE_URL is configured.
"""
import os
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from shared.observability import logger


class Base(DeclarativeBase):
    pass


# Import models to ensure they register on Base.metadata
from services.core_api.app.models import Company, Driver, Truck, Shipment
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

engine_kwargs = {"echo": False}
# SQLite's aiosqlite worker thread is non-daemon on Windows. A null pool
# closes every connection as soon as its request/session ends, which keeps
# CLI tests and packaged desktop tooling from hanging after successful work.
if db_url.startswith("sqlite"):
    engine_kwargs["poolclass"] = NullPool
engine = create_async_engine(db_url, **engine_kwargs)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_database():
    """Initializes all tables and schemas on startup, provisioning baseline demo entities if empty."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_tables_initialized")

    try:
        from sqlalchemy import select
        from services.core_api.app.auth import hash_password
        from services.core_api.app.models import Company, Driver, Truck, TruckStatus, Shipment

        async with async_session() as session:
            res = await session.execute(select(Company).where(Company.email == "ops@cargoresq.com"))
            company = res.scalar_one_or_none()
            if not company:
                company = Company(
                    id="comp_apex_pharma",
                    name="Apex Cold Logistics",
                    email="ops@cargoresq.com",
                    hashed_password=hash_password("Password123!"),
                    trust_score=99.4,
                )
                session.add(company)
                await session.flush()

                truck = Truck(
                    id="trk_9042",
                    company_id=company.id,
                    registration_number="MH-12-TX-9042",
                    latitude=18.5204,
                    longitude=73.8567,
                    status=TruckStatus.in_transit,
                    refrigerated=True,
                    min_temp_c=-20.0,
                    max_volume_m3=12.0,
                    max_weight_kg=3500.0,
                )
                truck2 = Truck(
                    id="trk_412",
                    company_id=company.id,
                    registration_number="KA-01-EQ-412",
                    latitude=12.9716,
                    longitude=77.5946,
                    status=TruckStatus.idle,
                    refrigerated=True,
                    min_temp_c=-25.0,
                    max_volume_m3=24.0,
                    max_weight_kg=7000.0,
                )
                session.add_all([truck, truck2])
                await session.flush()

                shipment = Shipment(
                    id="shp_8492",
                    owner_company_id=company.id,
                    truck_id=truck.id,
                    cargo_type="Refrigerated Insulin Vials",
                    requires_refrigeration=True,
                    required_max_temp_c=8.0,
                    volume_m3=3.5,
                    weight_kg=850.0,
                    value_inr=480000.0,
                    status="in_transit",
                )
                session.add(shipment)

                driver = Driver(
                    id="drv_rajesh",
                    company_id=company.id,
                    name="Rajesh Kumar",
                    email="rajesh@apexpharma.com",
                    hashed_password=hash_password("DriverPass123!"),
                    phone="+919840192831",
                    assigned_truck_id=truck.id,
                )
                session.add(driver)
                await session.commit()
                logger.info("demo_seed_provisioned", company="Apex Cold Logistics", driver="Rajesh Kumar")
    except Exception as e:
        logger.debug("demo_seed_skipped", error=str(e))

