"""
SOS API.

CargoResQ does not dispatch emergency services. Every response says so
explicitly (`psapDispatched: false`) and carries the public emergency numbers
for the device to dial, because a driver in trouble should reach 112 by
tapping once, not by remembering a number while their truck is on fire.

Authorisation for reading somebody else's SOS comes from the broadcast record:
a company may see an alert it does not own only if the fan-out decided to tell
them. There is no geo query on the read path and no guessing.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.auth import (
    actor_id_of,
    get_current_driver,
    get_current_principal,
)
from services.core_api.app.events.producer import event_producer
from services.core_api.app.models import Driver
from services.matching_engine.app.routing import haversine_distance_km
from services.telemetry.app.models import TruckLiveState
from shared.database import get_db
from shared.observability import logger

from .models import (
    EMERGENCY_NUMBERS,
    EmergencyContact,
    SosAlert,
    SosBroadcast,
    SosCategory,
    SosEvent,
    SosResponse,
    SosSeverity,
    SosStatus,
)
from .sos_service import (
    IllegalSosTransition,
    SosError,
    acknowledge,
    cancel,
    full_view,
    raise_sos,
    redacted_view,
    respond,
    set_status,
)

router = APIRouter(prefix="/api/v1/sos", tags=["sos"])


class ConditionIn(BaseModel):
    """What the driver can tell us about their own state.

    All optional. A driver with a broken arm should not be blocked by a
    required field, and "I don't know" is a legitimate answer to most of these.
    """

    model_config = ConfigDict(extra="forbid")

    persons_affected: Optional[int] = Field(None, ge=0, le=100)
    is_conscious: Optional[bool] = None
    is_breathing: Optional[bool] = None
    is_trapped: Optional[bool] = None
    is_mobile: Optional[bool] = None
    severe_bleeding: Optional[bool] = None
    note: Optional[str] = Field(None, max_length=512)
    vitals: Optional[Dict[str, Any]] = None


class RaiseSosRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: SosCategory
    severity: SosSeverity = SosSeverity.HIGH
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy_m: Optional[float] = Field(None, ge=0)
    location_source: str = Field("gps", max_length=16)
    landmark_note: Optional[str] = Field(None, max_length=256)
    condition: Optional[ConditionIn] = None
    #: Suppresses sound and push on the device. For a driver who must not be
    #: seen to have raised an alarm.
    silent_mode: bool = False
    network_broadcast: Optional[bool] = None
    client_request_id: Optional[str] = Field(None, max_length=128)


class RespondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., pattern="^(EN_ROUTE|ON_SCENE|STOOD_DOWN|UNABLE|INFO)$")
    eta_minutes: Optional[float] = Field(None, ge=0)
    truck_id: Optional[str] = None
    note: Optional[str] = Field(None, max_length=512)


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SosStatus
    resolution_code: Optional[str] = Field(None, max_length=32)
    resolution_note: Optional[str] = Field(None, max_length=512)


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin: str = Field(..., min_length=1, max_length=32)
    reason: Optional[str] = Field(None, max_length=256)


async def _driver_from_claims(claims: dict, db: AsyncSession) -> Driver:
    driver = await db.get(Driver, claims.get("principal_id"))
    if not driver or not driver.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Driver account is inactive"
        )
    return driver


@router.get("/emergency-numbers")
async def emergency_numbers():
    """Public emergency numbers for the device to dial.

    The device places the call. This API cannot and does not.
    """
    return {
        "numbers": EMERGENCY_NUMBERS,
        "notice": (
            "CargoResQ is not an emergency service and does not contact police, "
            "ambulance or fire services on your behalf. For a life-threatening "
            "emergency, dial 112."
        ),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_sos(
    req: RaiseSosRequest,
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    """Raise an SOS from a driver's device."""
    driver = await _driver_from_claims(claims, db)
    try:
        alert, created = await raise_sos(
            db,
            driver=driver,
            company_id=driver.company_id,
            created_by_type="driver",
            created_by_id=driver.id,
            category=req.category,
            severity=req.severity,
            latitude=req.latitude,
            longitude=req.longitude,
            accuracy_m=req.accuracy_m,
            location_source=req.location_source,
            landmark_note=req.landmark_note,
            condition=req.condition.model_dump() if req.condition else None,
            silent_mode=req.silent_mode,
            network_broadcast=req.network_broadcast,
            client_request_id=req.client_request_id,
            event_publisher=event_producer.publish,
        )
    except SosError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": str(exc), **exc.context},
        )

    payload = full_view(alert)
    payload["deduplicated"] = not created
    return payload


@router.get("/active")
async def active_alerts(
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Live SOS alerts raised by this company's own drivers."""
    result = await db.execute(
        select(SosAlert)
        .where(SosAlert.company_id == principal["company_id"])
        .where(
            SosAlert.status.not_in(
                [SosStatus.CLOSED.value, SosStatus.CANCELLED.value, SosStatus.RESOLVED.value]
            )
        )
        .order_by(SosAlert.received_at.desc())
    )
    return [full_view(a) for a in result.scalars().all()]


@router.get("/nearby")
async def nearby_alerts(
    limit: int = Query(20, ge=1, le=50),
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Cross-carrier alerts this company was told about.

    The pull counterpart to the live push, for a client that reconnects and
    needs to catch up. Redacted, because these are other companies' drivers.
    """
    company_id = principal["company_id"]
    result = await db.execute(
        select(SosAlert, SosBroadcast.distance_km_at_send)
        .join(SosBroadcast, SosBroadcast.sos_id == SosAlert.id)
        .where(SosBroadcast.recipient_company_id == company_id)
        .where(SosBroadcast.tier == "NETWORK")
        .where(
            SosAlert.status.not_in(
                [SosStatus.CLOSED.value, SosStatus.CANCELLED.value, SosStatus.RESOLVED.value]
            )
        )
        .order_by(SosAlert.received_at.desc())
        .limit(limit)
    )
    return [redacted_view(alert, distance) for alert, distance in result.all()]


async def _load_for_viewer(
    sos_id: str, principal: Dict[str, Any], db: AsyncSession
) -> tuple[SosAlert, str, Optional[float]]:
    """Return (alert, 'owner' | 'responder', distance_at_send)."""
    alert = await db.get(SosAlert, sos_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SOS not found")

    company_id = principal["company_id"]
    if alert.company_id == company_id:
        return alert, "owner", None

    told = await db.execute(
        select(SosBroadcast)
        .where(SosBroadcast.sos_id == sos_id)
        .where(SosBroadcast.recipient_company_id == company_id)
    )
    broadcast = told.scalars().first()
    if broadcast is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SOS not found")
    return alert, "responder", broadcast.distance_km_at_send


@router.get("/{sos_id}")
async def get_sos(
    sos_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alert, role, distance = await _load_for_viewer(sos_id, principal, db)
    if role == "owner":
        responses = await db.execute(
            select(SosResponse)
            .where(SosResponse.sos_id == sos_id)
            .order_by(SosResponse.created_at)
        )
        return full_view(alert, list(responses.scalars().all()))

    payload = redacted_view(alert, distance)
    # Once a responder has committed, they get what they need to actually
    # arrive: the exact location. Medical detail stays with the owner unless
    # the owner shares it.
    acked = await db.execute(
        select(SosResponse)
        .where(SosResponse.sos_id == sos_id)
        .where(SosResponse.responder_company_id == principal["company_id"])
    )
    if acked.scalars().first() is not None:
        payload["latitude"] = alert.latitude
        payload["longitude"] = alert.longitude
        payload["locationPrecision"] = "exact"
        payload["medicalShared"] = alert.medical_shared_at is not None
        if alert.medical_shared_at is not None:
            payload["condition"] = {
                "personsAffected": alert.persons_affected,
                "isConscious": alert.is_conscious,
                "isBreathing": alert.is_breathing,
                "isTrapped": alert.is_trapped,
                "severeBleeding": alert.severe_bleeding,
                "note": alert.condition_note,
            }
    return payload


@router.post("/{sos_id}/acknowledge")
async def acknowledge_sos(
    sos_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alert, _role, distance = await _load_for_viewer(sos_id, principal, db)
    alert = await acknowledge(
        db,
        alert,
        principal["company_id"],
        actor_id_of(principal),
        event_producer.publish,
    )
    return redacted_view(alert, distance)


@router.post("/{sos_id}/respond")
async def respond_to_sos(
    sos_id: str,
    req: RespondRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alert, _role, distance = await _load_for_viewer(sos_id, principal, db)
    alert = await respond(
        db,
        alert,
        principal["company_id"],
        actor_id_of(principal),
        action=req.action,
        eta_minutes=req.eta_minutes,
        truck_id=req.truck_id,
        note=req.note,
        event_publisher=event_producer.publish,
    )
    return redacted_view(alert, distance)


@router.post("/{sos_id}/status")
async def update_status(
    sos_id: str,
    req: StatusRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alert, role, _ = await _load_for_viewer(sos_id, principal, db)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the reporting company may change an SOS status",
        )
    try:
        alert = await set_status(
            db,
            alert,
            req.status,
            "company",
            actor_id_of(principal),
            req.resolution_code,
            req.resolution_note,
            event_producer.publish,
        )
    except IllegalSosTransition as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)}
        )
    return full_view(alert)


@router.post("/{sos_id}/cancel")
async def cancel_sos(
    sos_id: str,
    req: CancelRequest,
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    """Cancel an SOS. Only the reporting driver may do this.

    A wrong PIN on a critical alert returns success and does not cancel. Under
    duress the instruction is "cancel it", so the device must be able to
    appear to comply while the alert quietly escalates.
    """
    driver = await _driver_from_claims(claims, db)
    alert = await db.get(SosAlert, sos_id)
    if alert is None or alert.driver_id != driver.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SOS not found")

    from services.core_api.app.auth import verify_password

    pin_ok = verify_password(req.pin, driver.hashed_password)
    alert, cancelled = await cancel(
        db, alert, driver.id, pin_ok, req.reason, event_producer.publish
    )
    # The response is identical either way, deliberately.
    return {"id": alert.id, "status": "CANCELLED", "cancelledAt": None if not cancelled else True}


@router.post("/{sos_id}/share-medical")
async def share_medical(
    sos_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Release the driver's condition detail to acknowledged responders.

    An explicit act by the employing company, because this is sensitive
    personal data and a broadcast is not consent to publish it.
    """
    alert, role, _ = await _load_for_viewer(sos_id, principal, db)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the reporting company may share medical detail",
        )
    from datetime import datetime, timezone

    alert.medical_shared_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("sos_medical_shared", sos_id=sos_id, by=actor_id_of(principal))
    return {"id": alert.id, "medicalShared": True}


@router.get("/{sos_id}/timeline")
async def sos_timeline(
    sos_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alert, role, _ = await _load_for_viewer(sos_id, principal, db)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Timeline is owner-only"
        )
    result = await db.execute(
        select(SosEvent).where(SosEvent.sos_id == sos_id).order_by(SosEvent.created_at)
    )
    return [
        {
            "id": e.id,
            "type": e.type,
            "actorType": e.actor_type,
            "previousStatus": e.previous_status,
            "newStatus": e.new_status,
            "metadata": e.metadata_json,
            "at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# Emergency contacts
# ---------------------------------------------------------------------------

contacts_router = APIRouter(prefix="/api/v1/emergency-contacts", tags=["sos"])


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=128)
    phone: str = Field(..., min_length=3, max_length=32)
    relationship: Optional[str] = Field(None, max_length=64)
    driver_id: Optional[str] = None
    is_primary: bool = False
    notify_on_sos: bool = True


@contacts_router.get("")
async def list_contacts(
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Contacts for the caller.

    A driver sees their own contacts plus the company-wide ones; an operator
    sees everything the company holds.
    """
    company_id = principal["company_id"]
    stmt = select(EmergencyContact).where(EmergencyContact.company_id == company_id)

    if principal.get("principal_type") == "driver":
        driver_id = principal.get("principal_id")
        stmt = stmt.where(
            (EmergencyContact.driver_id == driver_id)
            | (EmergencyContact.driver_id.is_(None))
        )

    result = await db.execute(stmt.order_by(EmergencyContact.is_primary.desc()))
    return [
        {
            "id": c.id,
            "name": c.name,
            "phone": c.phone,
            "relationship": c.relationship,
            "driverId": c.driver_id,
            "isPrimary": c.is_primary,
            "notifyOnSos": c.notify_on_sos,
        }
        for c in result.scalars().all()
    ]


@contacts_router.post("", status_code=status.HTTP_201_CREATED)
async def create_contact(
    req: ContactIn,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    driver_id = req.driver_id
    if principal.get("principal_type") == "driver":
        # A driver may only add contacts for themselves.
        driver_id = principal.get("principal_id")

    contact = EmergencyContact(
        company_id=principal["company_id"],
        driver_id=driver_id,
        name=req.name,
        phone=req.phone,
        relationship=req.relationship,
        is_primary=req.is_primary,
        notify_on_sos=req.notify_on_sos,
    )
    db.add(contact)
    await db.commit()
    await db.refresh(contact)
    return {"id": contact.id, "name": contact.name, "phone": contact.phone}


@contacts_router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    contact = await db.get(EmergencyContact, contact_id)
    if contact is None or contact.company_id != principal["company_id"]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    if principal.get("principal_type") == "driver" and contact.driver_id != principal.get(
        "principal_id"
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found")
    await db.delete(contact)
    await db.commit()
