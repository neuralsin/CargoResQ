"""
Escrow and double-entry ledger domain models (Phase 5.1).

Every financial event records balancing debits and credits, and every escrow
names the two companies whose money it is -- without that, an escrow could not
be scoped to a tenant and the endpoints had to be left open.
"""
from datetime import datetime
import uuid
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.database import Base

#: Lifecycle of the held funds.
#:
#: CANCELLED exists because a bound rescue can fall through -- the rescuer
#: breaks down too, or the owner stands the job down. Without it a cancelled
#: rescue left its escrow stuck in ACCEPTED forever with no legal exit.
ESCROW_STATES = (
    "INITIATED",
    "ACCEPTED",
    "IN_TRANSIT",
    "PENDING_VERIFICATION",
    "RELEASED",
    "DISPUTED",
    "CANCELLED",
)


class Escrow(Base):
    __tablename__ = "escrows"
    __table_args__ = (
        CheckConstraint("amount_inr >= 0", name="chk_escrow_amount_nonneg"),
        CheckConstraint(
            "state IN ('INITIATED','ACCEPTED','IN_TRANSIT','PENDING_VERIFICATION',"
            "'RELEASED','DISPUTED','CANCELLED')",
            name="chk_escrow_state",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"esc_{uuid.uuid4().hex[:12]}"
    )
    # Retained for backwards compatibility with rows created before offers
    # existed. New escrows are identified by offer_id / incident_id.
    match_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    incident_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: The company whose cargo is stranded -- the payer.
    owner_company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: The company performing the rescue -- the payee.
    carrier_company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    amount_inr: Mapped[float] = mapped_column(Float, nullable=False)
    #: What the rescuing carrier receives. The difference between this and
    #: amount_inr is the platform fee, which only the owner side may see.
    carrier_payout_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")

    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="INITIATED")
    state_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    entries: Mapped[List["LedgerEntry"]] = relationship(
        back_populates="escrow", cascade="all, delete-orphan", order_by="LedgerEntry.created_at"
    )


class LedgerEntry(Base):
    """One side of a balanced posting.

    Entries are written in balanced pairs sharing a posting_ref, so the books
    can be proved: for any escrow, sum(debit) must equal sum(credit).
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("debit_inr >= 0 AND credit_inr >= 0", name="chk_ledger_nonneg"),
        CheckConstraint(
            "(debit_inr = 0) <> (credit_inr = 0)", name="chk_ledger_single_sided"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"led_{uuid.uuid4().hex[:12]}"
    )
    escrow_id: Mapped[str] = mapped_column(
        ForeignKey("escrows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Groups the two sides of one posting together.
    posting_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: What the posting represents: HOLD, RELEASE, REFUND, CANCEL.
    posting_type: Mapped[str] = mapped_column(String(24), nullable=False)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    debit_inr: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    credit_inr: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    memo: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    escrow: Mapped["Escrow"] = relationship(back_populates="entries")
