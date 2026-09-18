"""enable postgis

Revision ID: 0001_enable_postgis
Revises: 
Create Date: 2026-09-18 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "0001_enable_postgis"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostGIS extension initialization (Phase 1.4)
    # Check if dialect is postgresql before executing raw extension command
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS postgis;")
