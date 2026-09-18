"""
Breakdown Reporting API (Phase 2.2, 18.1, 18.6).
Rate-limited endpoint that ingests emergency breakdowns, deduplicates submissions,
and publishes 'breakdown.detected' to the event backbone.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Request, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address
from ..database import get_db
from ..models import Shipment
from ..auth import get_current_company
from ..events.producer import event_producer
from shared.events import EventEnvelope
from shared.idempotency import process_once
from shared.observability import INCIDENTS_TOTAL, logger
from services.orchestrator.app.orchestrator import create_incident
import uuid

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/v1/breakdowns", tags=["breakdowns"])


class BreakdownIn(BaseModel):
    shipment_id: str
    lat: float
    lng: float
    hours_to_spoilage: Optional[float] = None
    client_request_id: Optional[str] = None  # Idempotency key


async def ingest_breakdown(
    payload: BreakdownIn,
    company_id: str,
    db: AsyncSession,
    actor_id: str = "company_operator",
) -> dict:
    """Persist a breakdown, create its canonical incident, and emit events.

    This is shared by the desktop/ops endpoint and the dedicated driver app so
    both clients exercise exactly the same rescue workflow.
    """
    shipment = await db.get(Shipment, payload.shipment_id)
    if not shipment or shipment.owner_company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shipment not found or does not belong to your company",
        )

    event_id = payload.client_request_id or f"evt_{uuid.uuid4().hex[:8]}"

    async def _handle_breakdown():
        shipment.status = "breakdown_reported"
        await db.commit()

        cargo_class = "refrigerated" if shipment.requires_refrigeration else (
            "hazmat" if shipment.is_hazmat else "standard"
        )
        INCIDENTS_TOTAL.labels(cargo_class=cargo_class).inc()

        incident = await create_incident(
            session=db,
            shipment_id=payload.shipment_id,
            lat=payload.lat,
            lng=payload.lng,
            minutes_until_spoilage=(
                payload.hours_to_spoilage * 60
                if payload.hours_to_spoilage is not None
                else None
            ),
            actor_id=actor_id,
            event_publisher=event_producer.publish,
        )

        envelope = EventEnvelope(
            eventType="breakdown.detected",
            producer="core-api",
            eventId=event_id,
            payload={
                "eventId": event_id,
                "incidentId": incident.id,
                "shipmentId": payload.shipment_id,
                "companyId": company_id,
                "lat": payload.lat,
                "lng": payload.lng,
                "hoursToSpoilage": payload.hours_to_spoilage,
                "cargoType": shipment.cargo_type,
                "requiresRefrigeration": shipment.requires_refrigeration,
                "requiredMaxTempC": shipment.required_max_temp_c,
                "isHazmat": shipment.is_hazmat,
                "volumeM3": shipment.volume_m3,
                "weightKg": shipment.weight_kg,
                "valueInr": shipment.value_inr,
            },
        )
        await event_producer.publish("breakdown.detected", envelope.to_dict())
        logger.info(
            "breakdown_event_published",
            event_id=event_id,
            incident_id=incident.id,
            shipment_id=payload.shipment_id,
        )
        return incident

    was_processed = await process_once(
        session=db,
        event_id=event_id,
        consumer_name="breakdown_api_ingest",
        handler=_handle_breakdown,
    )

    return {
        "eventId": event_id,
        "shipmentId": payload.shipment_id,
        "status": "breakdown_registered" if was_processed else "duplicate_request_ignored",
        "wasProcessed": was_processed,
    }


@router.post("")
@limiter.limit("5/minute")
async def report_breakdown(
    request: Request,
    payload: BreakdownIn,
    company_id: str = Depends(get_current_company),
    db: AsyncSession = Depends(get_db),
):
    return await ingest_breakdown(payload, company_id, db)
