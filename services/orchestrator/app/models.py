"""
Incident Domain Models & State Transitions (Phase 11 & Phase 17).
Canonical source of truth for rescue incident lifecycles and append-only audit trail.
"""
from datetime import datetime
import enum
import uuid
from typing import Dict, Any, List
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Float, DateTime, ForeignKey, Enum as SAEnum, JSON, func, text


from shared.database import Base


class IncidentState(str, enum.Enum):
    NORMAL = "NORMAL"
    BREAKDOWN_REPORTED = "BREAKDOWN_REPORTED"
    TRIAGING = "TRIAGING"
    MATCHING = "MATCHING"
    RESCUE_OFFERED = "RESCUE_OFFERED"
    RESCUE_ACCEPTED = "RESCUE_ACCEPTED"
    DRIVER_EN_ROUTE = "DRIVER_EN_ROUTE"
    CARGO_TRANSFER = "CARGO_TRANSFER"
    RESCUE_IN_TRANSIT = "RESCUE_IN_TRANSIT"
    DELIVERED = "DELIVERED"
    VERIFICATION = "VERIFICATION"
    ESCROW_RELEASED = "ESCROW_RELEASED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"


TRANSITIONS = {
    IncidentState.NORMAL: {IncidentState.BREAKDOWN_REPORTED},
    IncidentState.BREAKDOWN_REPORTED: {IncidentState.TRIAGING, IncidentState.CANCELLED},
    IncidentState.TRIAGING: {IncidentState.MATCHING, IncidentState.CANCELLED},
    IncidentState.MATCHING: {IncidentState.RESCUE_OFFERED, IncidentState.CANCELLED},
    IncidentState.RESCUE_OFFERED: {
        IncidentState.RESCUE_ACCEPTED,
        IncidentState.MATCHING,
        IncidentState.CANCELLED,
    },
    # MATCHING is reachable again from here: a bound rescue can fall through
    # -- the rescuer breaks down too, or the owner stands the job down -- and
    # the cargo is then stranded exactly as it was before. Without this edge a
    # failed rescue left the incident wedged in RESCUE_ACCEPTED forever, with
    # no way to look for anyone else.
    IncidentState.RESCUE_ACCEPTED: {
        IncidentState.DRIVER_EN_ROUTE,
        IncidentState.MATCHING,
        IncidentState.CANCELLED,
    },
    IncidentState.DRIVER_EN_ROUTE: {
        IncidentState.CARGO_TRANSFER,
        IncidentState.MATCHING,
        IncidentState.CANCELLED,
    },
    IncidentState.CARGO_TRANSFER: {IncidentState.RESCUE_IN_TRANSIT, IncidentState.CANCELLED},
    IncidentState.RESCUE_IN_TRANSIT: {IncidentState.DELIVERED, IncidentState.CANCELLED},
    IncidentState.DELIVERED: {IncidentState.VERIFICATION},
    IncidentState.VERIFICATION: {IncidentState.ESCROW_RELEASED, IncidentState.DISPUTED},
    IncidentState.ESCROW_RELEASED: set(),
    IncidentState.DISPUTED: set(),
    IncidentState.CANCELLED: set(),
}


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"inc_{uuid.uuid4().hex[:10]}"
    )
    shipment_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # See Truck.status: VARCHAR + CHECK, not a native PostgreSQL ENUM.
    state: Mapped[IncidentState] = mapped_column(
        SAEnum(
            IncidentState,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=True,
            name="chk_incident_state",
        ),
        default=IncidentState.NORMAL,
        server_default=IncidentState.NORMAL.value,
        nullable=False,
        index=True,
    )
    assigned_truck_id: Mapped[str] = mapped_column(String(64), nullable=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    minutes_until_spoilage: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[List["IncidentEvent"]] = relationship(
        back_populates="incident", order_by="IncidentEvent.created_at", cascade="all, delete-orphan"
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=True)
    previous_state: Mapped[str] = mapped_column(String(32), nullable=True)
    new_state: Mapped[str] = mapped_column(String(32), nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=text("'{}'"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    incident: Mapped["Incident"] = relationship(back_populates="events")
