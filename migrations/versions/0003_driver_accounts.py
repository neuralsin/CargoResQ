"""driver accounts for the dedicated field app

Revision ID: 0003_driver_accounts
Revises: 0002_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_driver_accounts"
down_revision = "0002_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drivers",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_id", sa.String(64), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(128), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(256), nullable=False),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("assigned_truck_id", sa.String(64), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_drivers_company_id", "drivers", ["company_id"])
    op.create_index("ix_drivers_email", "drivers", ["email"])
    op.create_index("ix_drivers_assigned_truck_id", "drivers", ["assigned_truck_id"])


def downgrade() -> None:
    op.drop_index("ix_drivers_assigned_truck_id", table_name="drivers")
    op.drop_index("ix_drivers_email", table_name="drivers")
    op.drop_index("ix_drivers_company_id", table_name="drivers")
    op.drop_table("drivers")
