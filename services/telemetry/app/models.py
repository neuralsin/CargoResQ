"""
Telemetry: where the trucks actually are, and what state the cargo is in.

None of this existed. `trucks.latitude/longitude` were written once at
registration and never updated by any code path, so the fleet was frozen at
its creation coordinates while the UI described the view as live.

Position history is kept separately from the trucks table. Writing every ping
back onto `trucks` would churn the table (and, on PostGIS, the spatial index)
that the matching query scans -- so the hot working set lives in
`truck_live_state`, one row per truck, and `trucks` stays the registration and
capability record.
"""
from datetime import datetime
import enum
import uuid
from typing import Any, Dict, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database import Base

#: SQLite only auto-increments a column declared exactly INTEGER PRIMARY KEY;
#: a BIGINT primary key there is not a rowid alias and stays NULL on insert.
#: Postgres gets the 64-bit column these high-volume tables need.
AUTO_PK = BigInteger().with_variant(Integer, "sqlite")


class AlertType(str, enum.Enum):
    DWELL = "DWELL"
    ROUTE_DEVIATION = "ROUTE_DEVIATION"
    GPS_STALE = "GPS_STALE"
    IMPOSSIBLE_JUMP = "IMPOSSIBLE_JUMP"
    MOCK_LOCATION = "MOCK_LOCATION"
    TEMP_EXCURSION = "TEMP_EXCURSION"
    LOW_DEVICE_BATTERY = "LOW_DEVICE_BATTERY"
    DOOR_OPEN_UNSCHEDULED = "DOOR_OPEN_UNSCHEDULED"
    REEFER_FAULT = "REEFER_FAULT"


class AlertSeverity(str, enum.Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AlertStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    AUTO_RESOLVED = "AUTO_RESOLVED"


class TelemetryPing(Base):
    """One GPS fix reported by a driver's device.

    Append-only and high volume, so the primary key is an integer rather than
    a UUID string, and there is no spatial column -- this table is written
    constantly and queried spatially almost never.
    """

    __tablename__ = "telemetry_pings"
    __table_args__ = (
        CheckConstraint("latitude BETWEEN -90 AND 90", name="chk_ping_lat_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="chk_ping_lng_range"),
        CheckConstraint(
            "battery_pct IS NULL OR battery_pct BETWEEN 0 AND 100",
            name="chk_ping_battery_range",
        ),
        # Makes batch ingest idempotent: a device retrying an unacknowledged
        # batch cannot create duplicates.
        Index("uq_ping_device_client_id", "device_id", "client_ping_id", unique=True),
        Index("ix_pings_truck_time", "truck_id", "recorded_at"),
        Index("ix_pings_company_time", "company_id", "recorded_at"),
        Index("ix_pings_shipment_time", "shipment_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(AUTO_PK, primary_key=True, autoincrement=True)
    truck_id: Mapped[str] = mapped_column(
        ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False
    )
    driver_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    #: Denormalised for tenant scoping without a join on every read.
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    shipment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True
    )
    incident_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )

    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_ping_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: When the device took the fix, versus when the server received it. The
    #: gap is what distinguishes a live ping from a replayed offline queue.
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    speed_kph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    altitude_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    battery_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_charging: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    network_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    #: Android reports this directly. It is a far more reliable spoofing
    #: signal than inferring impossible speed, which the accuracy of a cheap
    #: GPS chip can produce on its own.
    mock_location: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    #: A ping that arrived long after it was taken. Recorded as history, but
    #: excluded from live detection -- a driver regaining signal after six
    #: hours offline must not detonate the alert system.
    is_backfill: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    app_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class CargoReading(Base):
    """One cargo-condition observation.

    Separate from position: a different producer, a different trust level, and
    a much longer retention, because this is the evidence an escrow settles on.
    """

    __tablename__ = "cargo_readings"
    __table_args__ = (
        CheckConstraint(
            "temperature_c IS NULL OR temperature_c BETWEEN -60 AND 90",
            name="chk_reading_temp_range",
        ),
        CheckConstraint(
            "humidity_pct IS NULL OR humidity_pct BETWEEN 0 AND 100",
            name="chk_reading_humidity_range",
        ),
        Index("uq_reading_sensor_client_id", "sensor_id", "client_reading_id", unique=True),
        Index("ix_readings_shipment_time", "shipment_id", "recorded_at"),
        Index("ix_readings_truck_time", "truck_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(AUTO_PK, primary_key=True, autoincrement=True)
    shipment_id: Mapped[str] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    truck_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("trucks.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )

    sensor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    client_reading_id: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    temperature_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    humidity_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    door_open: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    reefer_state: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    reefer_setpoint_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sensor_battery_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="device")
    #: A driver typing a number into a phone is not the same evidence as a
    #: signed feed from a reefer telematics unit, and escrow settlement should
    #: be able to tell the difference.
    trust_level: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="UNVERIFIED"
    )
    is_backfill: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )


class TruckLiveState(Base):
    """The current state of one truck -- the detection working set.

    One row per truck, updated in place. This is what matching and SOS
    proximity queries read, so it stays small and hot while the ping history
    grows without bound beside it.
    """

    __tablename__ = "truck_live_state"
    __table_args__ = (Index("ix_live_state_received", "last_received_at"),)

    truck_id: Mapped[str] = mapped_column(
        ForeignKey("trucks.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    last_ping_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_recorded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_received_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    speed_kph: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    battery_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_charging: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    is_moving: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    #: When the truck last started standing still, and where. Dwell duration
    #: is measured from here rather than recomputed from the ping history.
    dwell_started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dwell_anchor_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    dwell_anchor_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    last_temperature_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_reading_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TelemetryAlert(Base):
    """A derived condition worth a human's attention.

    One row per *episode*, not per observation. A truck standing still for six
    hours is one alert whose occurrence_count climbs, not three hundred and
    sixty alerts.
    """

    __tablename__ = "telemetry_alerts"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO','WARN','CRITICAL')", name="chk_alert_severity"
        ),
        CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','RESOLVED','AUTO_RESOLVED')",
            name="chk_alert_status",
        ),
        # An episode is unique only while it is live. Once resolved it drops
        # out of the index, so a genuinely new episode can open later.
        Index(
            "uq_alert_open_episode",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status IN ('OPEN','ACKNOWLEDGED')"),
            postgresql_where=text("status IN ('OPEN','ACKNOWLEDGED')"),
        ),
        Index("ix_alerts_company_status", "company_id", "status", "opened_at"),
        Index("ix_alerts_truck_type", "truck_id", "alert_type", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"alr_{uuid.uuid4().hex[:12]}"
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    truck_id: Mapped[str] = mapped_column(
        ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False
    )
    driver_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    shipment_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("shipments.id", ondelete="SET NULL"), nullable=True
    )
    incident_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )

    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OPEN")

    #: Identifies the episode. See detectors.episode_key.
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    #: Throttles notification without throttling detection.
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notify_cooldown_s: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="900"
    )
    peak_severity: Mapped[str] = mapped_column(String(16), nullable=False)

    first_value_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    last_value_json: Mapped[Dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)


class ShipmentRoute(Base):
    """The planned corridor for a shipment.

    Route deviation is undetectable without this: `shipments` has no
    destination, which is also why "route alignment" in the matching score had
    to be a hardcoded constant.
    """

    __tablename__ = "shipment_routes"
    __table_args__ = (Index("ix_routes_shipment_active", "shipment_id", "is_active"),)

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"rte_{uuid.uuid4().hex[:12]}"
    )
    shipment_id: Mapped[str] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lng: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lng: Mapped[float] = mapped_column(Float, nullable=False)
    #: Encoded polyline from the routing engine, when one was reachable.
    polyline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    corridor_buffer_m: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="3000"
    )
    planned_departure: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    planned_arrival: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="osrm")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ColdChainAttestation(Base):
    """An immutable finding about whether a shipment was carried in spec.

    Computed by the server from recorded readings, never supplied by a party
    to the transaction. This is what escrow settlement reads.
    """

    __tablename__ = "cold_chain_attestations"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"cca_{uuid.uuid4().hex[:12]}"
    )
    escrow_id: Mapped[str] = mapped_column(
        ForeignKey("escrows.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    shipment_id: Mapped[str] = mapped_column(
        ForeignKey("shipments.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True
    )

    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Copied at attestation time so a later edit to the shipment cannot
    #: retroactively change what was judged.
    required_max_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    readings_count: Mapped[int] = mapped_column(Integer, nullable=False)
    min_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    breach_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    breach_minutes: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    max_gap_minutes: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    #: What fraction of the carriage window actually has evidence. A log with
    #: three readings across eight hours proves very little.
    coverage_pct: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    lowest_trust_level: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="UNVERIFIED"
    )

    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    verdict_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    computed_by: Mapped[str] = mapped_column(String(64), nullable=False, server_default="system")
    #: SHA-256 over the ordered readings, so the finding is tamper-evident.
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
