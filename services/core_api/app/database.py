"""Re-export of the shared engine and session factory.

Kept so that the core_api modules can use relative imports. The schema itself
is managed by Alembic via shared.schema, not from here.
"""
from shared.database import async_session, engine, get_db

__all__ = ["engine", "async_session", "get_db"]
