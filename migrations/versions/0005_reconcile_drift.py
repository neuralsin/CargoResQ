"""reconcile model/migration drift

Revision ID: 0005_reconcile_drift
Revises: 0004_incident_escrow_tables
Create Date: 2026-09-18

The application previously called Base.metadata.create_all on every boot while
Alembic revisions existed alongside it. The schema you ended up with therefore
depended on how the database happened to be provisioned, and the two paths had
genuinely diverged: enum columns were native ENUM in the models but VARCHAR in
the migrations, several unique constraints and indexes existed on only one
side, and every server_default timestamp column disagreed on nullability.

This revision makes the migration lineage authoritative and matches it to the
models. It is written defensively with inspector checks because it may be
applied to a database created by either path.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_reconcile_drift"
down_revision = "0004_incident_escrow_tables"
branch_labels = None
depends_on = None


TIMESTAMP_NOT_NULL = [
    ("companies", "created_at"),
    ("drivers", "created_at"),
    ("trucks", None),
    ("shipments", "created_at"),
    ("incidents", "created_at"),
    ("incidents", "updated_at"),
    ("incident_events", "created_at"),
    ("escrows", "created_at"),
    ("escrows", "updated_at"),
    ("ledger_entries", "created_at"),
    ("processed_events", "processed_at"),
]

UNIQUE_INDEXES = [
    ("ix_companies_email", "companies", ["email"]),
    ("ix_drivers_email", "drivers", ["email"]),
    ("ix_trucks_registration_number", "trucks", ["registration_number"]),
]

PLAIN_INDEXES = [
    ("ix_shipments_owner_company_id", "shipments", ["owner_company_id"]),
    ("ix_shipments_truck_id", "shipments", ["truck_id"]),
    ("ix_incidents_state", "incidents", ["state"]),
]


def _existing_indexes(inspector, table):
    try:
        return {ix["name"] for ix in inspector.get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # 1. Timestamp columns carry a server_default but were nullable in the
    #    migrations and NOT NULL in the models.
    for table, column in TIMESTAMP_NOT_NULL:
        if column is None or table not in tables:
            continue
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column not in cols:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
                existing_server_default=sa.text("CURRENT_TIMESTAMP"),
            )

    # 2. incident_events.metadata_json must not be nullable.
    if "incident_events" in tables:
        with op.batch_alter_table("incident_events") as batch:
            batch.alter_column(
                "metadata_json",
                existing_type=sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )

    # 3. Email and registration indexes must be UNIQUE. Drop-and-recreate is
    #    required because the originals were created non-unique.
    for name, table, columns in UNIQUE_INDEXES:
        if table not in tables:
            continue
        if name in _existing_indexes(inspector, table):
            op.drop_index(name, table_name=table)
        op.create_index(name, table, columns, unique=True)

    # 4. Indexes present only on the model side.
    if "shipments" in tables:
        existing = _existing_indexes(inspector, "shipments")
        if "ix_shipments_owner" in existing:
            op.drop_index("ix_shipments_owner", table_name="shipments")
    for name, table, columns in PLAIN_INDEXES:
        if table not in tables:
            continue
        if name not in _existing_indexes(inspector, table):
            op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for name, table, _cols in UNIQUE_INDEXES:
        if table in tables and name in _existing_indexes(inspector, table):
            op.drop_index(name, table_name=table)
            op.create_index(name, table, [name.split("_")[-1]], unique=False)

    for name, table, _cols in PLAIN_INDEXES:
        if table in tables and name in _existing_indexes(inspector, table):
            op.drop_index(name, table_name=table)
