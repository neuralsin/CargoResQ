"""Dedicated field-driver API used by the Android CargoResQ Driver app."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_access_token, get_current_driver, hash_password, verify_password
from ..database import get_db
from ..models import Company, Driver, Shipment, Truck
from services.orchestrator.app.models import Incident
from .breakdowns import BreakdownIn, ingest_breakdown

router = APIRouter(prefix="/api/v1/driver", tags=["driver"])


class DriverRegisterRequest(BaseModel):
    company_id: str = Field(..., min_length=3)
    name: str = Field(..., min_length=2, max_length=128)
    email: EmailStr
    password: str = Field(..., min_length=8)
    phone: Optional[str] = Field(None, max_length=32)
    assigned_truck_id: Optional[str] = None


class DriverTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    driver_id: str
    company_id: str
    driver_name: str
    role: str = "DRIVER"


class DriverBreakdownRequest(BaseModel):
    shipment_id: Optional[str] = None
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    hours_to_spoilage: Optional[float] = Field(None, ge=0)
    client_request_id: Optional[str] = None


async def _driver_from_claims(claims: dict, db: AsyncSession) -> Driver:
    driver = await db.get(Driver, claims.get("principal_id"))
    if not driver or not driver.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Driver account is inactive")
    return driver


def _token_response(driver: Driver) -> DriverTokenResponse:
    return DriverTokenResponse(
        access_token=create_access_token(
            subject=driver.email,
            company_id=driver.company_id,
            role="DRIVER",
            principal_type="driver",
            principal_id=driver.id,
        ),
        driver_id=driver.id,
        company_id=driver.company_id,
        driver_name=driver.name,
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=DriverTokenResponse)
async def register_driver(req: DriverRegisterRequest, db: AsyncSession = Depends(get_db)):
    company = await db.get(Company, req.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    existing = await db.execute(select(Driver).where(Driver.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Driver with this email already exists")
    if req.assigned_truck_id:
        truck = await db.get(Truck, req.assigned_truck_id)
        if not truck or truck.company_id != company.id:
            raise HTTPException(status_code=400, detail="Assigned truck is not owned by this company")

    driver = Driver(
        company_id=company.id,
        name=req.name,
        email=req.email,
        hashed_password=hash_password(req.password),
        phone=req.phone,
        assigned_truck_id=req.assigned_truck_id,
    )
    db.add(driver)
    await db.commit()
    await db.refresh(driver)
    return _token_response(driver)


@router.post("/login", response_model=DriverTokenResponse)
async def login_driver(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Driver).where(Driver.email == form_data.username))
    driver = result.scalar_one_or_none()
    if not driver or not driver.active or not verify_password(form_data.password, driver.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect driver email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_response(driver)


@router.get("/me")
async def get_driver_me(
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    driver = await _driver_from_claims(claims, db)
    truck = await db.get(Truck, driver.assigned_truck_id) if driver.assigned_truck_id else None
    return {
        "id": driver.id,
        "name": driver.name,
        "email": driver.email,
        "phone": driver.phone,
        "companyId": driver.company_id,
        "assignedTruckId": driver.assigned_truck_id,
        "truck": _truck_payload(truck) if truck else None,
    }


@router.get("/active-shipment")
async def get_active_shipment(
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    driver = await _driver_from_claims(claims, db)
    if not driver.assigned_truck_id:
        return {"shipment": None}
    result = await db.execute(
        select(Shipment)
        .where(Shipment.owner_company_id == driver.company_id)
        .where(Shipment.truck_id == driver.assigned_truck_id)
        .where(Shipment.status.in_(["in_transit", "breakdown_reported"]))
        .order_by(Shipment.created_at.desc())
    )
    shipment = result.scalars().first()
    return {"shipment": _shipment_payload(shipment) if shipment else None}


@router.get("/active-incident")
async def get_active_incident(
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    driver = await _driver_from_claims(claims, db)
    result = await db.execute(
        select(Incident, Shipment)
        .join(Shipment, Shipment.id == Incident.shipment_id)
        .where(Shipment.owner_company_id == driver.company_id)
        .where(Shipment.truck_id == driver.assigned_truck_id)
        .where(Incident.state.not_in(["ESCROW_RELEASED", "DISPUTED", "CANCELLED"]))
        .order_by(Incident.created_at.desc())
    )
    pair = result.first()
    if not pair:
        return {"incident": None}
    incident, shipment = pair
    return {
        "incident": {
            "id": incident.id,
            "shipmentId": shipment.id,
            "cargoType": shipment.cargo_type,
            "state": incident.state.value,
            "minutesUntilSpoilage": incident.minutes_until_spoilage,
        }
    }


@router.post("/report-breakdown")
async def driver_report_breakdown(
    req: DriverBreakdownRequest,
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    driver = await _driver_from_claims(claims, db)
    shipment_id = req.shipment_id
    if not shipment_id:
        if not driver.assigned_truck_id:
            raise HTTPException(status_code=400, detail="Assign a truck or provide a shipment_id")
        result = await db.execute(
            select(Shipment)
            .where(Shipment.owner_company_id == driver.company_id)
            .where(Shipment.truck_id == driver.assigned_truck_id)
            .where(Shipment.status == "in_transit")
            .order_by(Shipment.created_at.desc())
        )
        shipment = result.scalars().first()
        if not shipment:
            raise HTTPException(status_code=404, detail="No active shipment assigned to this truck")
        shipment_id = shipment.id

    return await ingest_breakdown(
        BreakdownIn(
            shipment_id=shipment_id,
            lat=req.lat,
            lng=req.lng,
            hours_to_spoilage=req.hours_to_spoilage,
            client_request_id=req.client_request_id,
        ),
        company_id=driver.company_id,
        db=db,
        actor_id=driver.id,
    )


def _truck_payload(truck: Optional[Truck]) -> Optional[dict]:
    if not truck:
        return None
    return {
        "id": truck.id,
        "registrationNumber": truck.registration_number,
        "latitude": truck.latitude,
        "longitude": truck.longitude,
        "status": truck.status.value,
        "refrigerated": truck.refrigerated,
        "minTempC": truck.min_temp_c,
    }


def _shipment_payload(shipment: Optional[Shipment]) -> Optional[dict]:
    if not shipment:
        return None
    return {
        "id": shipment.id,
        "cargoType": shipment.cargo_type,
        "status": shipment.status,
        "requiresRefrigeration": shipment.requires_refrigeration,
        "requiredMaxTempC": shipment.required_max_temp_c,
        "isHazmat": shipment.is_hazmat,
        "volumeM3": shipment.volume_m3,
        "weightKg": shipment.weight_kg,
        "valueInr": shipment.value_inr,
    }
