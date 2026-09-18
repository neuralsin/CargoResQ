"""escrow ownership, balanced postings, boolean default repair

Revision ID: 0006_escrow_ownership
Revises: 0005_reconcile_drift
Create Date: 2026-09-18

An escrow could not be scoped to a tenant because it did not record whose
money it held -- `match_id` was an unconstrained string referencing nothing.
That is why every escrow endpoint had to be left unauthenticated. This adds
the owner/carrier company references, the incident link, and the payout split,
and gives ledger entries a posting_ref so the two sides of a posting can be
proved to balance.

It also repairs the boolean server defaults, which were written as the string
literal 'false'. SQLite stores that as text, and a non-empty string is truthy
-- any row inserted without an explicit value (raw SQL, a fixture, a restore)
would have come back as True.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_escrow_ownership"
down_revision = "0005_reconcile_drift"
branch_labels = None
depends_on = None


#: (table, column, intended default as 0/1, original string literal)
BOOLEAN_DEFAULT_REPAIRS = [
    ("trucks", "refrigerated", 0, "false"),
    ("trucks", "hazmat_certified", 0, "false"),
    ("shipments", "requires_refrigeration", 0, "false"),
    ("shipments", "is_hazmat", 0, "false"),
    ("drivers", "active", 1, "true"),
]


def upgrade() -> None:
    # --- escrows: who owes whom -----------------------------------------
    with op.batch_alter_table("escrows") as batch:
        batch.add_column(sa.Column("incident_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("owner_company_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("carrier_company_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("carrier_payout_inr", sa.Float(), nullable=True))
        batch.add_column(
            sa.Column("currency", sa.String(3), server_default="INR", nullable=False)
        )
        batch.add_column(sa.Column("state_reason", sa.String(256), nullable=True))
        # match_id is retained only for rows created before offers existed.
        batch.alter_column("match_id", existing_type=sa.VARCHAR(64), nullable=True)
        batch.create_foreign_key(
            "fk_escrows_incident", "incidents", ["incident_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_foreign_key(
            "fk_escrows_owner_company",
            "companies",
            ["owner_company_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_escrows_carrier_company",
            "companies",
            ["carrier_company_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint("chk_escrow_amount_nonneg", "amount_inr >= 0")
        batch.create_check_constraint(
            "chk_escrow_state",
            "state IN ('INITIATED','ACCEPTED','IN_TRANSIT','PENDING_VERIFICATION',"
            "'RELEASED','DISPUTED','CANCELLED')",
        )

    op.create_index("ix_escrows_incident_id", "escrows", ["incident_id"])
    op.create_index("ix_escrows_owner_company_id", "escrows", ["owner_company_id"])
    op.create_index("ix_escrows_carrier_company_id", "escrows", ["carrier_company_id"])

    # --- ledger_entries: provable postings -------------------------------
    # Existing rows are legacy single postings; backfill them with their own
    # escrow id as the posting reference so nothing is left null.
    with op.batch_alter_table("ledger_entries") as batch:
        batch.add_column(
            sa.Column("posting_ref", sa.String(64), nullable=False, server_default="legacy")
        )
        batch.add_column(
            sa.Column("posting_type", sa.String(24), nullable=False, server_default="RELEASE")
        )
        batch.add_column(sa.Column("memo", sa.String(256), nullable=True))
        batch.alter_column(
            "debit_inr", existing_type=sa.FLOAT(), nullable=False, server_default="0"
        )
        batch.alter_column(
            "credit_inr", existing_type=sa.FLOAT(), nullable=False, server_default="0"
        )
        batch.create_check_constraint(
            "chk_ledger_nonneg", "debit_inr >= 0 AND credit_inr >= 0"
        )
        batch.create_check_constraint(
            "chk_ledger_single_sided", "(debit_inr = 0) <> (credit_inr = 0)"
        )

    op.execute("UPDATE ledger_entries SET posting_ref = escrow_id WHERE posting_ref = 'legacy'")
    op.create_index("ix_ledger_entries_posting_ref", "ledger_entries", ["posting_ref"])

    with op.batch_alter_table("ledger_entries") as batch:
        batch.alter_column("posting_ref", existing_type=sa.String(64), server_default=None)
        batch.alter_column("posting_type", existing_type=sa.String(24), server_default=None)

    # --- boolean default repair ------------------------------------------
    for table, column, intended, _original in BOOLEAN_DEFAULT_REPAIRS:
        op.execute(
            f"UPDATE {table} SET {column} = 0 WHERE {column} IN ('false', 'False', '0', '')"
        )
        op.execute(f"UPDATE {table} SET {column} = 1 WHERE {column} IN ('true', 'True', '1')")
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=sa.Boolean(),
                existing_nullable=False,
                server_default=sa.text(str(intended)),
            )


def downgrade() -> None:
    for table, column, _intended, original in BOOLEAN_DEFAULT_REPAIRS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=sa.Boolean(),
                existing_nullable=False,
                server_default=original,
            )

    op.drop_index("ix_ledger_entries_posting_ref", table_name="ledger_entries")
    with op.batch_alter_table("ledger_entries") as batch:
        batch.drop_constraint("chk_ledger_single_sided", type_="check")
        batch.drop_constraint("chk_ledger_nonneg", type_="check")
        batch.drop_column("memo")
        batch.drop_column("posting_type")
        batch.drop_column("posting_ref")

    op.drop_index("ix_escrows_carrier_company_id", table_name="escrows")
    op.drop_index("ix_escrows_owner_company_id", table_name="escrows")
    op.drop_index("ix_escrows_incident_id", table_name="escrows")
    with op.batch_alter_table("escrows") as batch:
        batch.drop_constraint("chk_escrow_state", type_="check")
        batch.drop_constraint("chk_escrow_amount_nonneg", type_="check")
        batch.drop_constraint("fk_escrows_carrier_company", type_="foreignkey")
        batch.drop_constraint("fk_escrows_owner_company", type_="foreignkey")
        batch.drop_constraint("fk_escrows_incident", type_="foreignkey")
        batch.drop_column("state_reason")
        batch.drop_column("currency")
        batch.drop_column("carrier_payout_inr")
        batch.drop_column("carrier_company_id")
        batch.drop_column("owner_company_id")
        batch.drop_column("incident_id")
