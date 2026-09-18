"""
Shipments API Router.
Enforces multi-tenant data boundaries on cargo shipments.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Shipment
from ..auth import get_current_company

router = APIRouter(prefix="/api/v1/shipments", tags=["shipments"])


class CreateShipmentRequest(BaseModel):
    truck_id: Optional[str] = None
    cargo_type: str = Field(..., min_length=2)
    requires_refrigeration: bool = False
    required_max_temp_c: Optional[float] = None
    is_hazmat: bool = False
    volume_m3: float = Field(..., gt=0)
    weight_kg: float = Field(..., gt=0)
    value_inr: float = Field(..., ge=0)


class ShipmentResponse(BaseModel):
    id: str
    owner_company_id: str
    truck_id: Optional[str]
    cargo_type: str
    requires_refrigeration: bool
    required_max_temp_c: Optional[float]
    is_hazmat: bool
    volume_m3: float
    weight_kg: float
    value_inr: float
    status: str


@router.get("", response_model=List[ShipmentResponse])
async def list_company_shipments(
    company_id: str = Depends(get_current_company),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Shipment).where(Shipment.owner_company_id == company_id)
    res = await db.execute(stmt)
    shipments = res.scalars().all()
    return [
        ShipmentResponse(
            id=s.id,
            owner_company_id=s.owner_company_id,
            truck_id=s.truck_id,
            cargo_type=s.cargo_type,
            requires_refrigeration=s.requires_refrigeration,
            required_max_temp_c=s.required_max_temp_c,
            is_hazmat=s.is_hazmat,
            volume_m3=s.volume_m3,
            weight_kg=s.weight_kg,
            value_inr=s.value_inr,
            status=s.status,
        )
        for s in shipments
    ]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ShipmentResponse)
async def create_shipment(
    req: CreateShipmentRequest,
    company_id: str = Depends(get_current_company),
    db: AsyncSession = Depends(get_db),
):
    shipment = Shipment(
        owner_company_id=company_id,
        truck_id=req.truck_id,
        cargo_type=req.cargo_type,
        requires_refrigeration=req.requires_refrigeration,
        required_max_temp_c=req.required_max_temp_c,
        is_hazmat=req.is_hazmat,
        volume_m3=req.volume_m3,
        weight_kg=req.weight_kg,
        value_inr=req.value_inr,
        status="in_transit",
    )
    db.add(shipment)
    await db.commit()
    await db.refresh(shipment)

    return ShipmentResponse(
        id=shipment.id,
        owner_company_id=shipment.owner_company_id,
        truck_id=shipment.truck_id,
        cargo_type=shipment.cargo_type,
        requires_refrigeration=shipment.requires_refrigeration,
        required_max_temp_c=shipment.required_max_temp_c,
        is_hazmat=shipment.is_hazmat,
        volume_m3=shipment.volume_m3,
        weight_kg=shipment.weight_kg,
        value_inr=shipment.value_inr,
        status=shipment.status,
    )
