"""
Telemetry API.

Ingest is driver-authenticated and the truck is derived from the driver's
assignment, so a device cannot report positions for somebody else's vehicle.

One cross-tenant read exists deliberately: while a rescue is bound, the
stranded owner may watch the rescuer approach. That is the whole point of
waiting for help. It is narrowed to position only, and only for the duration
of the rescue.
"""
from datetime import datetime, timedelta, timezone
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
from services.core_api.app.models import Driver, Shipment, Truck
from services.orchestrator.app.offer_models import OfferState, RescueOffer
from shared.database import get_db
from shared.observability import logger

from .alerts import alert_view, list_alerts
from .ingest import MAX_BATCH_SIZE, ingest_pings, ingest_readings
from .models import AlertStatus, CargoReading, TelemetryAlert, TelemetryPing, TruckLiveState

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


class PingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_ping_id: str = Field(..., max_length=64)
    recorded_at: datetime
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy_m: Optional[float] = Field(None, ge=0)
    speed_kph: Optional[float] = Field(None, ge=0)
    heading_deg: Optional[float] = Field(None, ge=0, le=360)
    altitude_m: Optional[float] = None
    battery_pct: Optional[float] = Field(None, ge=0, le=100)
    is_charging: Optional[bool] = None
    network_type: Optional[str] = Field(None, max_length=16)
    provider: Optional[str] = Field(None, max_length=16)
    #: Reported by the device. Trusted as a signal precisely because an honest
    #: client has no reason to set it and a naive spoofer does not clear it.
    mock_location: bool = False
    shipment_id: Optional[str] = None
    incident_id: Optional[str] = None
    app_version: Optional[str] = Field(None, max_length=32)


class PingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(..., max_length=64)
    pings: List[PingIn] = Field(..., min_length=1, max_length=MAX_BATCH_SIZE)


class ReadingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_reading_id: str = Field(..., max_length=64)
    shipment_id: str
    recorded_at: datetime
    temperature_c: Optional[float] = Field(None, ge=-60, le=90)
    humidity_pct: Optional[float] = Field(None, ge=0, le=100)
    door_open: Optional[bool] = None
    reefer_state: Optional[str] = Field(None, max_length=16)
    reefer_setpoint_c: Optional[float] = None
    sensor_battery_pct: Optional[float] = Field(None, ge=0, le=100)
    source: str = Field("device", max_length=16)
    incident_id: Optional[str] = None


class ReadingBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_id: str = Field(..., max_length=64)
    readings: List[ReadingIn] = Field(..., min_length=1, max_length=MAX_BATCH_SIZE)


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_note: Optional[str] = Field(None, max_length=256)


async def _driver_from_claims(claims: dict, db: AsyncSession) -> Driver:
    driver = await db.get(Driver, claims.get("principal_id"))
    if not driver or not driver.active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Driver account is inactive"
        )
    return driver


@router.post("/pings")
async def post_pings(
    batch: PingBatch,
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    """Accept a batch of position fixes from a driver's device.

    Not rate-limited by IP: behind any proxy every device shares one address,
    and throttling the fleet's telemetry to one device's budget would make the
    map wrong for everyone.
    """
    driver = await _driver_from_claims(claims, db)
    result = await ingest_pings(
        db,
        driver,
        batch.device_id,
        [p.model_dump() for p in batch.pings],
        event_publisher=event_producer.publish,
    )
    return result


@router.post("/readings")
async def post_readings(
    batch: ReadingBatch,
    claims: dict = Depends(get_current_driver),
    db: AsyncSession = Depends(get_db),
):
    """Accept cargo-condition observations."""
    driver = await _driver_from_claims(claims, db)
    return await ingest_readings(
        db,
        driver,
        batch.sensor_id,
        [r.model_dump() for r in batch.readings],
        event_publisher=event_producer.publish,
    )


@router.get("/config")
async def telemetry_config(claims: dict = Depends(get_current_driver)):
    """Server-driven capture settings.

    Lets the cadence be changed for the whole fleet without shipping an app
    update, which matters the first time telemetry volume becomes a problem.
    """
    return {
        "pingIntervalSeconds": 30,
        "pingIntervalWhileStationarySeconds": 300,
        "conditionIntervalSeconds": 300,
        "batchSize": 50,
        "maxBatchSize": MAX_BATCH_SIZE,
        "uploadOnlyOnWifi": False,
        "queueLimit": 2000,
    }


async def _may_read_truck(
    truck_id: str, principal: Dict[str, Any], db: AsyncSession
) -> Truck:
    """Own fleet always; another company's truck only while it is rescuing you."""
    truck = await db.get(Truck, truck_id)
    if truck is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found")

    company_id = principal.get("company_id")
    if truck.company_id == company_id:
        return truck

    # The stranded owner watching their rescuer arrive.
    bound = await db.execute(
        select(RescueOffer)
        .where(RescueOffer.carrier_truck_id == truck_id)
        .where(RescueOffer.owner_company_id == company_id)
        .where(RescueOffer.state == OfferState.BOUND.value)
    )
    if bound.scalars().first() is not None:
        return truck

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found")


@router.get("/trucks/{truck_id}/live")
async def truck_live(
    truck_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    truck = await _may_read_truck(truck_id, principal, db)
    live = await db.get(TruckLiveState, truck_id)
    own_fleet = truck.company_id == principal.get("company_id")

    if live is None or live.latitude is None:
        return {
            "truckId": truck_id,
            "hasFix": False,
            # Falling back to the registration coordinates would look like a
            # position. Saying there is no fix is the honest answer.
            "registeredLat": truck.latitude if own_fleet else None,
            "registeredLng": truck.longitude if own_fleet else None,
        }

    payload = {
        "truckId": truck_id,
        "hasFix": True,
        "latitude": live.latitude,
        "longitude": live.longitude,
        "headingDeg": live.heading_deg,
        "speedKph": live.speed_kph,
        "isMoving": live.is_moving,
        "lastSeenAt": live.last_received_at.isoformat() if live.last_received_at else None,
    }
    if own_fleet:
        # Device battery and cargo temperature belong to the operating
        # company, not to whoever they happen to be rescuing.
        payload.update(
            {
                "batteryPct": live.battery_pct,
                "isCharging": live.is_charging,
                "lastTemperatureC": live.last_temperature_c,
                "dwellSince": (
                    live.dwell_started_at.isoformat() if live.dwell_started_at else None
                ),
            }
        )
    return payload


@router.get("/trucks/{truck_id}/track")
async def truck_track(
    truck_id: str,
    hours: float = Query(6.0, gt=0, le=168),
    limit: int = Query(500, ge=1, le=5000),
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Recent position history, for drawing a trail on the map."""
    await _may_read_truck(truck_id, principal, db)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(TelemetryPing)
        .where(TelemetryPing.truck_id == truck_id)
        .where(TelemetryPing.recorded_at >= since)
        .order_by(TelemetryPing.recorded_at)
        .limit(limit)
    )
    return [
        {
            "lat": p.latitude,
            "lng": p.longitude,
            "at": p.recorded_at.isoformat() if p.recorded_at else None,
            "speedKph": p.speed_kph,
        }
        for p in result.scalars().all()
    ]


@router.get("/fleet/live")
async def fleet_live(
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Every truck this company operates, with its current position."""
    company_id = principal.get("company_id")
    result = await db.execute(
        select(Truck, TruckLiveState)
        .outerjoin(TruckLiveState, TruckLiveState.truck_id == Truck.id)
        .where(Truck.company_id == company_id)
    )
    fleet = []
    for truck, live in result.all():
        has_fix = live is not None and live.latitude is not None
        fleet.append(
            {
                "truckId": truck.id,
                "registrationNumber": truck.registration_number,
                "status": truck.status.value,
                "refrigerated": truck.refrigerated,
                "hasFix": has_fix,
                "latitude": live.latitude if has_fix else truck.latitude,
                "longitude": live.longitude if has_fix else truck.longitude,
                # The map should show a stale position differently from a live
                # one, so it says which this is rather than quietly mixing them.
                "positionIsLive": has_fix,
                "lastSeenAt": (
                    live.last_received_at.isoformat()
                    if has_fix and live.last_received_at
                    else None
                ),
                "speedKph": live.speed_kph if has_fix else None,
                "headingDeg": live.heading_deg if has_fix else None,
                "lastTemperatureC": live.last_temperature_c if live else None,
            }
        )
    return fleet


@router.get("/shipments/{shipment_id}/cold-chain")
async def shipment_cold_chain(
    shipment_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """The recorded condition log for a shipment, and what it amounts to."""
    shipment = await db.get(Shipment, shipment_id)
    if shipment is None or shipment.owner_company_id != principal.get("company_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shipment not found")

    result = await db.execute(
        select(CargoReading)
        .where(CargoReading.shipment_id == shipment_id)
        .order_by(CargoReading.recorded_at)
    )
    readings = list(result.scalars().all())
    temps = [r.temperature_c for r in readings if r.temperature_c is not None]
    limit = shipment.required_max_temp_c

    return {
        "shipmentId": shipment_id,
        "requiredMaxTempC": limit,
        "readingCount": len(readings),
        "minTempC": min(temps) if temps else None,
        "maxTempC": max(temps) if temps else None,
        "meanTempC": round(sum(temps) / len(temps), 2) if temps else None,
        "breachCount": len([t for t in temps if limit is not None and t > limit]),
        "readings": [
            {
                "at": r.recorded_at.isoformat() if r.recorded_at else None,
                "temperatureC": r.temperature_c,
                "humidityPct": r.humidity_pct,
                "doorOpen": r.door_open,
                "reeferState": r.reefer_state,
                "trustLevel": r.trust_level,
            }
            for r in readings
        ],
    }


@router.get("/alerts")
async def get_alerts(
    status_filter: Optional[str] = Query(None, alias="status"),
    severity: Optional[str] = Query(None),
    alert_type: Optional[str] = Query(None, alias="type"),
    truck_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alerts = await list_alerts(
        db,
        company_id=principal["company_id"],
        status=status_filter,
        severity=severity,
        alert_type=alert_type,
        truck_id=truck_id,
        limit=limit,
    )
    return [alert_view(a) for a in alerts]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alert = await _own_alert(alert_id, principal, db)
    alert.status = AlertStatus.ACKNOWLEDGED.value
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by = actor_id_of(principal)
    await db.commit()
    await db.refresh(alert)
    return alert_view(alert)


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    req: ResolveRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    alert = await _own_alert(alert_id, principal, db)
    alert.status = AlertStatus.RESOLVED.value
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolution_note = req.resolution_note
    await db.commit()
    await db.refresh(alert)
    logger.info("telemetry_alert_resolved", alert_id=alert_id, by=actor_id_of(principal))
    return alert_view(alert)


async def _own_alert(
    alert_id: str, principal: Dict[str, Any], db: AsyncSession
) -> TelemetryAlert:
    alert = await db.get(TelemetryAlert, alert_id)
    if alert is None or alert.company_id != principal.get("company_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return alert
