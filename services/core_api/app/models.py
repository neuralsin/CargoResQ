"""
Core Domain Models (Phase 1.3 & Phase 18.4).
Persisted via SQLAlchemy 2.0 with strict database-level constraints.
"""
from datetime import datetime
import enum
import uuid
from typing import List, Optional
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import (
    String,
    Float,
    Boolean,
    ForeignKey,
    Enum as SAEnum,
    DateTime,
    CheckConstraint,
    func,
    text,
)


from shared.database import Base


class TruckStatus(str, enum.Enum):
    idle = "idle"
    in_transit = "in_transit"
    dispatched_rescue = "dispatched_rescue"


class Company(Base):
    __tablename__ = "companies"
    # `email` already declares unique=True, index=True, which emits a unique
    # index. A second table-level UniqueConstraint on the same column is
    # redundant and showed up permanently as schema drift.

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"comp_{uuid.uuid4().hex[:8]}"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32), default="CARRIER_OWNER", server_default="CARRIER_OWNER", nullable=False
    )
    trust_score: Mapped[float] = mapped_column(
        Float, default=100.0, server_default="100.0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    trucks: Mapped[List["Truck"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    shipments: Mapped[List["Shipment"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    drivers: Mapped[List["Driver"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class Driver(Base):
    """A field operator identity used by the dedicated driver app."""

    __tablename__ = "drivers"  # see Company.email re: unique index

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"drv_{uuid.uuid4().hex[:8]}"
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    assigned_truck_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("1"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    company: Mapped["Company"] = relationship(back_populates="drivers")


class Truck(Base):
    __tablename__ = "trucks"
    __table_args__ = (
        # registration_number declares unique=True, index=True already.
        CheckConstraint("max_volume_m3 > 0", name="chk_truck_volume_positive"),
        CheckConstraint("max_weight_kg > 0", name="chk_truck_weight_positive"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"trk_{uuid.uuid4().hex[:8]}"
    )
    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    registration_number: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # native_enum=False stores a VARCHAR with a CHECK constraint rather than a
    # PostgreSQL ENUM type. Python still sees TruckStatus; the database stays
    # portable and needs no type migration when a status is added.
    status: Mapped[TruckStatus] = mapped_column(
        SAEnum(
            TruckStatus,
            native_enum=False,
            length=32,
            validate_strings=True,
            create_constraint=True,
            name="chk_truck_status",
        ),
        default=TruckStatus.idle,
        server_default=TruckStatus.idle.value,
        nullable=False,
        index=True,
    )
    refrigerated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    min_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hazmat_certified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    max_volume_m3: Mapped[float] = mapped_column(Float, nullable=False)
    max_weight_kg: Mapped[float] = mapped_column(Float, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="trucks")
    shipments: Mapped[List["Shipment"]] = relationship(back_populates="truck")


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint("volume_m3 > 0", name="chk_shipment_volume_positive"),
        CheckConstraint("weight_kg > 0", name="chk_shipment_weight_positive"),
        CheckConstraint("value_inr >= 0", name="chk_shipment_value_nonneg"),
        CheckConstraint(
            "destination_lat IS NULL OR destination_lat BETWEEN -90 AND 90",
            name="chk_shipment_dest_lat_range",
        ),
        CheckConstraint(
            "destination_lng IS NULL OR destination_lng BETWEEN -180 AND 180",
            name="chk_shipment_dest_lng_range",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"shp_{uuid.uuid4().hex[:8]}"
    )
    owner_company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    truck_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("trucks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    cargo_type: Mapped[str] = mapped_column(String(128), nullable=False)
    requires_refrigeration: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    required_max_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_hazmat: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    volume_m3: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    value_inr: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="in_transit", server_default="in_transit", nullable=False
    )

    # Where the load is going. Absent until now, which is why "route
    # alignment" in the matching score had to be a hardcoded constant and
    # route-deviation detection was impossible -- there was no route.
    origin_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    origin_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    destination_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    destination_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    destination_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    planned_arrival_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    company: Mapped["Company"] = relationship(back_populates="shipments")
    truck: Mapped[Optional["Truck"]] = relationship(back_populates="shipments")


class StorageFacility(Base):
    """A place cargo can be put when it cannot continue its journey.

    Not every breakdown has a rescue. Sometimes no compatible truck is within
    reach of a load that has ninety minutes of cold left, and the choice is
    between spoiling it on the hard shoulder and getting it into a chiller
    forty minutes away. The second option is worth far more than a perfect
    rescue that arrives too late, so the network has to know where those
    chillers are.

    Facilities are shared infrastructure rather than any one carrier's
    property: a bonded warehouse will take a competitor's pallets for a
    handling fee, which is the whole reason a relay works. `operator_company_id`
    records who runs it when that is a member company, and is null for the
    third-party depots that make up most of the network.
    """

    __tablename__ = "storage_facilities"
    __table_args__ = (
        CheckConstraint("capacity_m3 > 0", name="chk_facility_capacity_positive"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"stor_{uuid.uuid4().hex[:8]}"
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: Null for a third-party depot that belongs to no member company.
    operator_company_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    #: What the site can actually hold. A facility that cannot take the cargo
    #: is not a fallback, so these are matched against the shipment the same
    #: way a truck's capacity is.
    refrigerated: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    min_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hazmat_approved: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("0"), nullable=False
    )
    capacity_m3: Mapped[float] = mapped_column(Float, nullable=False)
    available_m3: Mapped[float] = mapped_column(Float, nullable=False)

    #: What it costs to put a load in, per cubic metre per day. Shown to the
    #: owner next to the spoilage they are avoiding, because the decision is
    #: always that comparison and never the fee on its own.
    handling_fee_inr: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0"), nullable=False
    )
    storage_fee_inr_per_m3_day: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0"), nullable=False
    )

    #: A depot with a closed gate at 2am is not an option at 2am.
    open_24h: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("1"), nullable=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("1"), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
