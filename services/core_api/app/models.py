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
    UniqueConstraint,
    func,
)


class Base(DeclarativeBase):
    pass


class TruckStatus(str, enum.Enum):
    idle = "idle"
    in_transit = "in_transit"
    dispatched_rescue = "dispatched_rescue"


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("email", name="uq_company_email"),
    )

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: f"comp_{uuid.uuid4().hex[:8]}"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    email: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="CARRIER_OWNER")
    trust_score: Mapped[float] = mapped_column(Float, default=100.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    trucks: Mapped[List["Truck"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    shipments: Mapped[List["Shipment"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class Truck(Base):
    __tablename__ = "trucks"
    __table_args__ = (
        UniqueConstraint("registration_number", name="uq_truck_registration"),
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
    status: Mapped[TruckStatus] = mapped_column(
        SAEnum(TruckStatus), default=TruckStatus.idle, nullable=False, index=True
    )
    refrigerated: Mapped[bool] = mapped_column(Boolean, default=False)
    min_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    hazmat_certified: Mapped[bool] = mapped_column(Boolean, default=False)
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
    requires_refrigeration: Mapped[bool] = mapped_column(Boolean, default=False)
    required_max_temp_c: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_hazmat: Mapped[bool] = mapped_column(Boolean, default=False)
    volume_m3: Mapped[float] = mapped_column(Float, nullable=False)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    value_inr: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="in_transit")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    company: Mapped["Company"] = relationship(back_populates="shipments")
    truck: Mapped[Optional["Truck"]] = relationship(back_populates="shipments")
