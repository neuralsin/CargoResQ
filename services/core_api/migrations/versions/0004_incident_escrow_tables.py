"""incident orchestration and escrow ledger tables

Revision ID: 0004_incident_escrow_tables
Revises: 0003_driver_accounts
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_incident_escrow_tables"
down_revision = "0003_driver_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("shipment_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="NORMAL"),
        sa.Column("assigned_truck_id", sa.String(64), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("minutes_until_spoilage", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_incidents_shipment_id", "incidents", ["shipment_id"])

    op.create_table(
        "incident_events",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("incident_id", sa.String(64), sa.ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=True),
        sa.Column("previous_state", sa.String(32), nullable=True),
        sa.Column("new_state", sa.String(32), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_incident_events_incident_id", "incident_events", ["incident_id"])

    op.create_table(
        "escrows",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("match_id", sa.String(64), nullable=False),
        sa.Column("amount_inr", sa.Float(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="INITIATED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("escrow_id", sa.String(64), sa.ForeignKey("escrows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("debit_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("credit_inr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_ledger_entries_escrow_id", "ledger_entries", ["escrow_id"])


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_escrow_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_table("escrows")
    op.drop_index("ix_incident_events_incident_id", table_name="incident_events")
    op.drop_table("incident_events")
    op.drop_index("ix_incidents_shipment_id", table_name="incidents")
    op.drop_table("incidents")
