"""
SOS: a driver in trouble, and who gets told.

WHAT THIS IS NOT
----------------
CargoResQ does not dispatch emergency services. There is no integration with
India's ERSS/112, with any state PSAP, with 108 ambulance services, or with
any government dispatch system, and nothing in this package creates one.

Pressing SOS does three things:

  1. broadcasts inside the CargoResQ network -- the driver's own company, and
     nearby carriers who may be able to physically help;
  2. invokes whatever outbound notifier adapters the operator has configured
     (SMS, webhook, voice) against numbers the operator supplies;
  3. hands the driver's device a one-tap dial to 112. The *phone* places that
     call. This backend does not, and cannot.

Every response carries `psap_dispatched: false` so no client can present this
as an emergency service by accident.

PRIVACY
-------
Driver condition and vitals are sensitive personal data. They are visible to
the reporting company and to responders the reporting company has explicitly
shared them with -- never in a cross-carrier broadcast payload.
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


class SosCategory(str, enum.Enum):
    MEDICAL = "MEDICAL"
    ACCIDENT = "ACCIDENT"
    POLICE_SECURITY = "POLICE_SECURITY"
    FIRE = "FIRE"
    MECHANICAL = "MECHANICAL"
    CARGO_RISK = "CARGO_RISK"
    OTHER = "OTHER"


class SosSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MODERATE = "MODERATE"


class SosStatus(str, enum.Enum):
    NEW = "NEW"
    BROADCASTING = "BROADCASTING"
    UNANSWERED = "UNANSWERED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESPONDER_EN_ROUTE = "RESPONDER_EN_ROUTE"
    ON_SCENE = "ON_SCENE"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    ESCALATED = "ESCALATED"


SOS_TRANSITIONS: Dict[SosStatus, set] = {
    SosStatus.NEW: {SosStatus.BROADCASTING, SosStatus.CANCELLED},
    SosStatus.BROADCASTING: {
        SosStatus.ACKNOWLEDGED,
        SosStatus.UNANSWERED,
        SosStatus.ESCALATED,
        SosStatus.RESOLVED,
        SosStatus.CANCELLED,
    },
    SosStatus.UNANSWERED: {
        SosStatus.ACKNOWLEDGED,
        SosStatus.ESCALATED,
        SosStatus.RESOLVED,
        SosStatus.CANCELLED,
    },
    SosStatus.ACKNOWLEDGED: {
        SosStatus.RESPONDER_EN_ROUTE,
        SosStatus.ON_SCENE,
        SosStatus.ESCALATED,
        SosStatus.RESOLVED,
        SosStatus.CANCELLED,
    },
    SosStatus.RESPONDER_EN_ROUTE: {
        SosStatus.ON_SCENE,
        SosStatus.ACKNOWLEDGED,
        SosStatus.ESCALATED,
        SosStatus.RESOLVED,
        SosStatus.CANCELLED,
    },
    SosStatus.ON_SCENE: {SosStatus.RESOLVED, SosStatus.ESCALATED},
    SosStatus.ESCALATED: {
        SosStatus.ACKNOWLEDGED,
        SosStatus.RESPONDER_EN_ROUTE,
        SosStatus.ON_SCENE,
        SosStatus.RESOLVED,
    },
    SosStatus.RESOLVED: {SosStatus.CLOSED},
    SosStatus.CLOSED: set(),
    SosStatus.CANCELLED: set(),
}

#: How far a category is broadcast by default, in kilometres.
DEFAULT_BROADCAST_RADIUS_KM = {
    SosCategory.MEDICAL: 25.0,
    SosCategory.ACCIDENT: 25.0,
    SosCategory.FIRE: 25.0,
    # Deliberately tighter, and off by default -- see network_broadcast below.
    SosCategory.POLICE_SECURITY: 15.0,
    SosCategory.MECHANICAL: 50.0,
    SosCategory.CARGO_RISK: 50.0,
    SosCategory.OTHER: 25.0,
}

#: India's public emergency numbers, served to the device for one-tap dialling.
#: The device places the call; this backend never does.
EMERGENCY_NUMBERS = [
    {"label": "All emergencies (ERSS)", "number": "112", "primary": True},
    {"label": "Ambulance", "number": "108", "primary": False},
    {"label": "Police", "number": "100", "primary": False},
    {"label": "Fire", "number": "101", "primary": False},
    {"label": "Highway assistance", "number": "1033", "primary": False},
]


class SosAlert(Base):
    __tablename__ = "sos_alerts"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="chk_sos_lat_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="chk_sos_lng_range"),
        CheckConstraint(
            "severity IN ('CRITICAL','HIGH','MODERATE')", name="chk_sos_severity"
        ),
        Index("uq_sos_client_request", "client_request_id", unique=True),
        Index("ix_sos_company_status", "company_id", "status", "received_at"),
        Index("ix_sos_status_time", "status", "received_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"sos_{uuid.uuid4().hex[:12]}"
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    driver_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    truck_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("trucks.id", ondelete="SET NULL"), nullable=True
    )
    shipment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True
    )
    incident_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )

    category: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="NEW")

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="gps")
    address_text: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    #: Free text from the driver: "km 42 marker, southbound". Often more
    #: actionable to a nearby truck than a coordinate.
    landmark_note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # --- driver-reported condition (SENSITIVE, own company only) ---------
    persons_affected: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_conscious: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_breathing: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_trapped: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_mobile: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    severe_bleeding: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    condition_note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    vitals_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    #: Blood group, allergies, emergency contact -- copied at creation so a
    #: responder is not blocked on a profile lookup.
    driver_medical_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    #: Set when the reporting company shares medical detail with responders.
    medical_shared_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Duress mode: no device sound, no push notification back to the phone.
    #: Under a hijack, the coerced instruction is "cancel it", so the device
    #: must be able to appear to comply.
    silent_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    broadcast_radius_km: Mapped[float] = mapped_column(Float, nullable=False)
    #: Whether nearby competitors are told at all. Off by default for
    #: POLICE_SECURITY: broadcasting "hijack in progress, here are the
    #: coordinates" to an unvetted network is itself a risk.
    network_broadcast: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )

    ack_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    responder_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    broadcast_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_ack_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_on_scene_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: If nobody has acknowledged by this time, widen the radius and fire the
    #: outbound notifiers.
    auto_escalate_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    resolution_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: A panicking driver taps SOS five times. The device sends the same id,
    #: and repeats return the existing alert instead of creating five.
    client_request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SosBroadcast(Base):
    """Who was told about an SOS, through what channel, and whether it landed.

    Also the authorisation record: a company may see an SOS it does not own
    only if a row here says it was told. No geo maths on the read path, and no
    guessing.
    """

    __tablename__ = "sos_broadcasts"
    __table_args__ = (
        Index(
            "uq_sos_broadcast_target",
            "sos_id",
            "recipient_company_id",
            "channel",
            "adapter_name",
            unique=True,
        ),
        Index("ix_sos_broadcast_recipient", "recipient_company_id", "sent_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"snd_{uuid.uuid4().hex[:12]}"
    )
    sos_id: Mapped[str] = mapped_column(
        ForeignKey("sos_alerts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recipient_company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    recipient_driver_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    #: OWN_COMPANY | NETWORK | PLATFORM_OPS | EXTERNAL
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter_name: Mapped[str] = mapped_column(String(48), nullable=False, server_default="-")
    #: FULL for the reporting company; REDACTED for the network.
    redaction_level: Mapped[str] = mapped_column(String(16), nullable=False)
    distance_km_at_send: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    adapter_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="PENDING"
    )
    adapter_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


class SosResponse(Base):
    """A company offering, or withdrawing, help."""

    __tablename__ = "sos_responses"
    __table_args__ = (
        CheckConstraint(
            "action IN ('ACKNOWLEDGE','EN_ROUTE','ON_SCENE','STOOD_DOWN','UNABLE','INFO')",
            name="chk_sos_response_action",
        ),
        Index("ix_sos_responses_sos", "sos_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"rsp_{uuid.uuid4().hex[:12]}"
    )
    sos_id: Mapped[str] = mapped_column(
        ForeignKey("sos_alerts.id", ondelete="CASCADE"), nullable=False
    )
    responder_company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    responder_driver_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    responder_truck_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("trucks.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    eta_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    distance_km: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SosEvent(Base):
    """Append-only audit trail, mirroring incident_events."""

    __tablename__ = "sos_events"
    __table_args__ = (Index("ix_sos_events_sos", "sos_id", "created_at"),)

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"sev_{uuid.uuid4().hex[:12]}"
    )
    sos_id: Mapped[str] = mapped_column(
        ForeignKey("sos_alerts.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    previous_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    new_status: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EmergencyContact(Base):
    """Who to call for a given driver or company."""

    __tablename__ = "emergency_contacts"
    __table_args__ = (Index("ix_contacts_scope", "company_id", "driver_id"),)

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"ecx_{uuid.uuid4().hex[:12]}"
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    #: Null means the contact belongs to the company as a whole.
    driver_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("drivers.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    relationship: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    notify_on_sos: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
