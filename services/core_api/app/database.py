"""
Database Engine & Async Session Management (Phase 1).
Delegates to unified shared.database for zero-config hosting and unified data models.
"""
from shared.database import engine, async_session, get_db, init_database as init_db

__all__ = ["engine", "async_session", "get_db", "init_db"]
