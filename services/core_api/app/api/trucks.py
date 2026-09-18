"""
Truck Fleet Management Endpoints (Phase 1.3 & Phase 1.5).
Strictly scopes query access by current company ID.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Truck, TruckStatus
from ..auth import get_current_company

router = APIRouter(prefix="/api/v1/trucks", tags=["trucks"])


class CreateTruckRequest(BaseModel):
    registration_number: str = Field(..., min_length=3)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    status: TruckStatus = TruckStatus.idle
    refrigerated: bool = False
    min_temp_c: Optional[float] = None
    hazmat_certified: bool = False
    max_volume_m3: float = Field(..., gt=0)
    max_weight_kg: float = Field(..., gt=0)


class TruckResponse(BaseModel):
    id: str
    company_id: str
    registration_number: str
    latitude: float
    longitude: float
    status: str
    refrigerated: bool
    min_temp_c: Optional[float]
    hazmat_certified: bool
    max_volume_m3: float
    max_weight_kg: float


@router.get("", response_model=List[TruckResponse])
async def list_company_trucks(
    company_id: str = Depends(get_current_company),
    db: AsyncSession = Depends(get_db),
):
    """
    Enforces isolation at the query layer:
    WHERE company_id = :company_id
    """
    stmt = select(Truck).where(Truck.company_id == company_id)
    res = await db.execute(stmt)
    trucks = res.scalars().all()
    return [
        TruckResponse(
            id=t.id,
            company_id=t.company_id,
            registration_number=t.registration_number,
            latitude=t.latitude,
            longitude=t.longitude,
            status=t.status.value,
            refrigerated=t.refrigerated,
            min_temp_c=t.min_temp_c,
            hazmat_certified=t.hazmat_certified,
            max_volume_m3=t.max_volume_m3,
            max_weight_kg=t.max_weight_kg,
        )
        for t in trucks
    ]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TruckResponse)
async def register_truck(
    req: CreateTruckRequest,
    company_id: str = Depends(get_current_company),
    db: AsyncSession = Depends(get_db),
):
    # Check registration uniqueness
    existing = await db.execute(
        select(Truck).where(Truck.registration_number == req.registration_number)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Truck with this registration number already registered",
        )

    truck = Truck(
        company_id=company_id,
        registration_number=req.registration_number,
        latitude=req.latitude,
        longitude=req.longitude,
        status=req.status,
        refrigerated=req.refrigerated,
        min_temp_c=req.min_temp_c,
        hazmat_certified=req.hazmat_certified,
        max_volume_m3=req.max_volume_m3,
        max_weight_kg=req.max_weight_kg,
    )
    db.add(truck)
    await db.commit()
    await db.refresh(truck)

    return TruckResponse(
        id=truck.id,
        company_id=truck.company_id,
        registration_number=truck.registration_number,
        latitude=truck.latitude,
        longitude=truck.longitude,
        status=truck.status.value,
        refrigerated=truck.refrigerated,
        min_temp_c=truck.min_temp_c,
        hazmat_certified=truck.hazmat_certified,
        max_volume_m3=truck.max_volume_m3,
        max_weight_kg=truck.max_weight_kg,
    )
