"""
Reputation: earned from recorded behaviour, not asserted.

`companies.trust_score` existed but was written once by the seed and never
again, and the endpoint that exposed it took the statistics as a request body
-- so a company's reputation was whatever the caller said it was. The
fulfilment-rating endpoint went further and returned invented numbers
(4.96 stars, 1240 trips, a fabricated driver leaderboard) for any company id,
including ones that did not exist.

Here reputation is derived from rows: offers actually received and accepted,
rescues actually completed, disputes actually raised, and ratings both parties
actually left. Both sides rate each other, because a carrier's experience of a
bad customer is as real as the reverse.
"""
from datetime import datetime
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import (
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


class RescueRating(Base):
    """One party's rating of the other, after a completed rescue."""

    __tablename__ = "rescue_ratings"
    __table_args__ = (
        CheckConstraint("stars BETWEEN 1 AND 5", name="chk_rating_stars_range"),
        CheckConstraint(
            "rater_role IN ('owner','carrier')", name="chk_rating_rater_role"
        ),
        # One rating per party per offer. Rating someone twice to move their
        # average is the most obvious way to game this.
        Index("uq_rating_offer_rater", "offer_id", "rater_role", unique=True),
        Index("ix_ratings_subject", "subject_company_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"rat_{uuid.uuid4().hex[:12]}"
    )
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("rescue_offers.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    #: Which side of the transaction is speaking.
    rater_role: Mapped[str] = mapped_column(String(16), nullable=False)
    rater_company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    subject_company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )

    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Structured, so reputation can say *why* rather than only how many stars:
    #: "on_time", "cargo_handled_well", "late", "condition_breach", ...
    tags_json: Mapped[Any] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    comment: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompanyReputation(Base):
    """Rolled-up, recomputed reputation for one company.

    A materialised view in table form: cheap to read on the offer card, where
    the owner is deciding whether to trust a stranger with their cargo.
    """

    __tablename__ = "company_reputation"

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )

    # --- responsiveness -------------------------------------------------
    offers_received: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    offers_accepted: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    offers_declined: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    offers_expired: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    avg_response_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # --- delivery -------------------------------------------------------
    rescues_completed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rescues_cancelled_after_bind: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    disputes_raised_against: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    condition_breaches: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    # --- peer ratings ---------------------------------------------------
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rating_sum: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # --- derived --------------------------------------------------------
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="100.0")
    #: The human-readable reasons behind the number. Showing a bare score
    #: without its basis gives the owner nothing to reason about.
    signals_json: Mapped[Any] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def acceptance_rate(self) -> Optional[float]:
        if not self.offers_received:
            return None
        return self.offers_accepted / self.offers_received

    @property
    def average_stars(self) -> Optional[float]:
        if not self.rating_count:
            return None
        return self.rating_sum / self.rating_count

    def to_public_dict(self) -> Dict[str, Any]:
        """What a prospective counterparty may see.

        Deliberately includes the sample size. "5.0 stars" from one rating and
        from two hundred are not the same claim, and hiding the denominator is
        how rating displays mislead.
        """
        return {
            "companyId": self.company_id,
            "trustScore": round(self.trust_score, 1),
            "averageStars": round(self.average_stars, 2) if self.average_stars else None,
            "ratingCount": self.rating_count,
            "rescuesCompleted": self.rescues_completed,
            "acceptanceRate": (
                round(self.acceptance_rate, 3) if self.acceptance_rate is not None else None
            ),
            "offersReceived": self.offers_received,
            "avgResponseSeconds": (
                round(self.avg_response_seconds) if self.avg_response_seconds else None
            ),
            "disputesAgainst": self.disputes_raised_against,
            "cancelledAfterBind": self.rescues_cancelled_after_bind,
            "signals": self.signals_json or [],
            "computedAt": self.computed_at.isoformat() if self.computed_at else None,
            "isNewCounterparty": self.rescues_completed == 0 and self.rating_count == 0,
        }
