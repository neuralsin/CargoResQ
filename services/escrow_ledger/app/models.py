"""
Escrow & Double-Entry Ledger Domain Models (Phase 5.1).
Every financial event records balancing debits and credits.
"""
from datetime import datetime
import uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Float, DateTime, ForeignKey, func


from shared.database import Base


class Escrow(Base):
    __tablename__ = "escrows"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    match_id: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_inr: Mapped[float] = mapped_column(Float, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="INITIATED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="escrow", cascade="all, delete-orphan"
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    escrow_id: Mapped[str] = mapped_column(
        ForeignKey("escrows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # e.g. "payer_hold", "payee_receivable"
    debit_inr: Mapped[float] = mapped_column(Float, default=0.0)
    credit_inr: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    escrow: Mapped["Escrow"] = relationship(back_populates="entries")
