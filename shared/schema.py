"""
Schema lifecycle.

Alembic owns the schema. The application never calls create_all. On a
development boot the upgrade runs automatically so `python main.py` still just
works; in production the process refuses to start against a database whose
revision does not match the code, rather than serving traffic on a schema it
was not written for.
"""
import asyncio
from pathlib import Path
from typing import Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from services.core_api.app.config import is_production, settings
from shared.database import engine
from shared.observability import logger

REPO_ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

#: The last revision that a database provisioned by the old
#: Base.metadata.create_all path is schema-equivalent to. Such a database has
#: the tables but no alembic_version row, so it is stamped here before the
#: reconciliation revision is applied on top.
LEGACY_CREATE_ALL_EQUIVALENT = "0004_incident_escrow_tables"


def alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def _inspect_sync(conn) -> tuple[Optional[str], bool]:
    """Return (current_revision, has_application_tables)."""
    ctx = MigrationContext.configure(conn)
    current = ctx.get_current_revision()
    tables = set(inspect(conn).get_table_names())
    return current, "companies" in tables


async def current_revision() -> tuple[Optional[str], bool]:
    async with engine.connect() as conn:
        return await conn.run_sync(_inspect_sync)


def _upgrade_sync(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)


def _stamp_sync(revision: str) -> None:
    command.stamp(alembic_config(), revision)


async def ensure_schema() -> None:
    """Bring the database to the code's schema revision, or refuse to start.

    Handles three cases:
      * empty database        -> upgrade from scratch
      * pre-Alembic database  -> stamp as legacy-equivalent, then upgrade
      * managed database      -> upgrade (dev) or verify (production)
    """
    revision, has_tables = await current_revision()
    head = head_revision()

    if revision == head:
        logger.info("schema_up_to_date", revision=revision)
        return

    if revision is None and has_tables:
        # Provisioned by the old create_all path: the tables exist but no
        # revision was ever recorded. Adopt it rather than failing.
        logger.warn("adopting_pre_alembic_database", stamping=LEGACY_CREATE_ALL_EQUIVALENT)
        await asyncio.to_thread(_stamp_sync, LEGACY_CREATE_ALL_EQUIVALENT)
        revision = LEGACY_CREATE_ALL_EQUIVALENT

    if is_production() and not settings.run_migrations_on_boot:
        raise RuntimeError(
            f"Database is at revision {revision!r} but this build expects {head!r}. "
            "Run `alembic upgrade head` as a deployment step, or set "
            "RUN_MIGRATIONS_ON_BOOT=1 to apply migrations at startup."
        )

    logger.info("running_migrations", frm=revision, to=head)
    await asyncio.to_thread(_upgrade_sync, "head")
    logger.info("schema_migrated", revision=head)
