"""
Rescue offers: the two-way handshake.

A rescue binds only when *both* sides have said yes -- the stranded cargo
owner and the helping carrier. Previously a single unauthenticated call could
set an incident to RESCUE_ACCEPTED and assign another company's truck to it,
with no offer record, no consent from the rescuer, and no expiry.

The two acceptances are independent, order-free columns rather than a linear
state machine, which means "carrier accepts then owner confirms" and "owner
pre-confirms then carrier accepts" are the same code path.
"""
from datetime import datetime
import enum
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base


class OfferState(str, enum.Enum):
    PENDING = "PENDING"
    CARRIER_ACCEPTED = "CARRIER_ACCEPTED"
    OWNER_CONFIRMED = "OWNER_CONFIRMED"
    BOUND = "BOUND"
    DECLINED = "DECLINED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


#: States from which an offer can still go somewhere.
LIVE_OFFER_STATES = frozenset(
    {OfferState.PENDING, OfferState.CARRIER_ACCEPTED, OfferState.OWNER_CONFIRMED}
)

TERMINAL_OFFER_STATES = frozenset(
    {
        OfferState.DECLINED,
        OfferState.WITHDRAWN,
        OfferState.EXPIRED,
        OfferState.SUPERSEDED,
        OfferState.CANCELLED,
    }
)

OFFER_TRANSITIONS: Dict[OfferState, set] = {
    OfferState.PENDING: {
        OfferState.CARRIER_ACCEPTED,
        OfferState.OWNER_CONFIRMED,
        OfferState.DECLINED,
        OfferState.WITHDRAWN,
        OfferState.EXPIRED,
        OfferState.SUPERSEDED,
    },
    OfferState.CARRIER_ACCEPTED: {
        OfferState.BOUND,
        OfferState.DECLINED,
        OfferState.WITHDRAWN,
        OfferState.EXPIRED,
        OfferState.SUPERSEDED,
    },
    OfferState.OWNER_CONFIRMED: {
        OfferState.BOUND,
        OfferState.DECLINED,
        OfferState.WITHDRAWN,
        OfferState.EXPIRED,
        OfferState.SUPERSEDED,
    },
    # A bound rescue can still fall through -- the rescuer breaks down, or the
    # owner stands the job down -- but it cannot quietly revert to an offer.
    OfferState.BOUND: {OfferState.CANCELLED},
    OfferState.DECLINED: set(),
    OfferState.WITHDRAWN: set(),
    OfferState.EXPIRED: set(),
    OfferState.SUPERSEDED: set(),
    OfferState.CANCELLED: set(),
}

DEFAULT_OFFER_TTL_SECONDS = 300


class RescueOffer(Base):
    """One offer of help, from one carrier's truck, for one incident."""

    __tablename__ = "rescue_offers"
    __table_args__ = (
        CheckConstraint("price_total_inr >= 0", name="chk_offer_price_nonneg"),
        CheckConstraint("carrier_payout_inr >= 0", name="chk_offer_payout_nonneg"),
        CheckConstraint(
            "carrier_payout_inr <= price_total_inr", name="chk_offer_payout_within_price"
        ),
        CheckConstraint(
            "state IN ('PENDING','CARRIER_ACCEPTED','OWNER_CONFIRMED','BOUND',"
            "'DECLINED','WITHDRAWN','EXPIRED','SUPERSEDED','CANCELLED')",
            name="chk_offer_state",
        ),
        # At most one bound offer per incident. Two carriers racing to accept
        # is resolved by the database, not by a read-then-write in Python.
        Index(
            "uq_offer_bound_per_incident",
            "incident_id",
            unique=True,
            sqlite_where=text("state = 'BOUND'"),
            postgresql_where=text("state = 'BOUND'"),
        ),
        # At most one offer per incident may carry the owner's confirmation
        # while still live, so the owner cannot pre-confirm two carriers.
        Index(
            "uq_offer_owner_confirmed_per_incident",
            "incident_id",
            unique=True,
            sqlite_where=text(
                "owner_confirmed_at IS NOT NULL AND state IN "
                "('PENDING','CARRIER_ACCEPTED','OWNER_CONFIRMED','BOUND')"
            ),
            postgresql_where=text(
                "owner_confirmed_at IS NOT NULL AND state IN "
                "('PENDING','CARRIER_ACCEPTED','OWNER_CONFIRMED','BOUND')"
            ),
        ),
        Index("uq_offer_incident_truck_round", "incident_id", "carrier_truck_id", "offer_round", unique=True),
        Index("ix_offers_carrier_inbox", "carrier_company_id", "state", "expires_at"),
        Index("ix_offers_expiry_sweep", "state", "expires_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"off_{uuid.uuid4().hex[:12]}"
    )
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shipment_id: Mapped[str] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Offers are fanned out in rounds; if a whole round expires the owner can
    #: open another without colliding with the previous one's unique index.
    offer_round: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    #: Denormalised so authorisation is a single-row lookup rather than a join
    #: through incidents -> shipments on every request.
    owner_company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    carrier_company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    carrier_truck_id: Mapped[str] = mapped_column(
        ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    carrier_driver_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=OfferState.PENDING.value
    )

    # --- money ---------------------------------------------------------
    # Three fields with three audiences. The owner sees what they pay, the
    # carrier sees what they earn, and nobody but the owner sees the margin.
    price_total_inr: Mapped[float] = mapped_column(Float, nullable=False)
    carrier_payout_inr: Mapped[float] = mapped_column(Float, nullable=False)
    platform_fee_inr: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    price_breakdown_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")

    # --- non-financial terms (visible to carrier and driver) -------------
    eta_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    distance_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rescue_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score_reasons_json: Mapped[Any] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )

    # --- the handshake ---------------------------------------------------
    carrier_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    carrier_accepted_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    owner_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_confirmed_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    bound_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    terminal_reason: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    terminal_note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    terminated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    escrow_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("escrows.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    #: A company rescuing its own stranded cargo. Allowed, and still requires
    #: both calls, so there is exactly one code path.
    intra_company: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # -- derived -----------------------------------------------------------
    @property
    def is_bound(self) -> bool:
        return self.state == OfferState.BOUND.value

    @property
    def both_parties_agreed(self) -> bool:
        return self.carrier_accepted_at is not None and self.owner_confirmed_at is not None
