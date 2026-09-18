"""initial schema

Revision ID: 0002_initial_schema
Revises: 0001_enable_postgis
Create Date: 2026-09-18 10:05:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_initial_schema"
down_revision = "0001_enable_postgis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Companies table
    op.create_table(
        "companies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("email", sa.String(128), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), server_default="CARRIER_OWNER", nullable=False),
        sa.Column("trust_score", sa.Float(), server_default="100.0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_companies_email", "companies", ["email"])

    # 2. Trucks table
    op.create_table(
        "trucks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("company_id", sa.String(64), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("registration_number", sa.String(64), nullable=False, unique=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), server_default="idle", nullable=False),
        sa.Column("refrigerated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("min_temp_c", sa.Float(), nullable=True),
        sa.Column("hazmat_certified", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("max_volume_m3", sa.Float(), nullable=False),
        sa.Column("max_weight_kg", sa.Float(), nullable=False),
        sa.CheckConstraint("max_volume_m3 > 0", name="chk_truck_volume_positive"),
        sa.CheckConstraint("max_weight_kg > 0", name="chk_truck_weight_positive"),
    )
    op.create_index("ix_trucks_company_id", "trucks", ["company_id"])
    op.create_index("ix_trucks_status", "trucks", ["status"])

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Add geography point column and GIST spatial index (Phase 3.1)
        op.execute("ALTER TABLE trucks ADD COLUMN location geography(Point, 4326);")
        op.execute("UPDATE trucks SET location = ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography;")
        op.execute("CREATE INDEX idx_trucks_location ON trucks USING GIST (location);")

    # 3. Shipments table
    op.create_table(
        "shipments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("owner_company_id", sa.String(64), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("truck_id", sa.String(64), sa.ForeignKey("trucks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("cargo_type", sa.String(128), nullable=False),
        sa.Column("requires_refrigeration", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("required_max_temp_c", sa.Float(), nullable=True),
        sa.Column("is_hazmat", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("volume_m3", sa.Float(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("value_inr", sa.Float(), nullable=False),
        sa.Column("status", sa.String(32), server_default="in_transit", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("volume_m3 > 0", name="chk_shipment_volume_positive"),
        sa.CheckConstraint("weight_kg > 0", name="chk_shipment_weight_positive"),
        sa.CheckConstraint("value_inr >= 0", name="chk_shipment_value_nonneg"),
    )
    op.create_index("ix_shipments_owner", "shipments", ["owner_company_id"])

    # 4. Processed Events table (Phase 18.1 Idempotency)
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("consumer", sa.String(128), primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
    op.drop_table("shipments")
    op.drop_table("trucks")
    op.drop_table("companies")
