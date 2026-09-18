"""
Incident orchestrator API.

Every route is authenticated and scoped to the company that owns the cargo.
Previously create, advance, read, timeline and SLA were all open, and the
audit trail's `actor_id` was a free-text request field defaulting to the
literal "ops" -- so an anonymous caller could drive any carrier's incident to
ESCROW_RELEASED and the trail would record it as an internal action.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.auth import actor_id_of, get_current_company, get_current_principal
from services.core_api.app.events.producer import event_producer
from services.core_api.app.models import Shipment
from shared.database import get_db
from shared.observability import logger

from .authz import load_incident_for_owner
from .dispatch import escrow_for_incident, sync_escrow_to_incident
from .models import Incident, IncidentState
from .orchestrator import (
    IllegalTransition,
    IncidentNotFound,
    advance,
    create_incident,
    get_incident_timeline,
)
from .sla import compute_sla

router = APIRouter(prefix="/api/v1", tags=["orchestrator"])


class CreateIncidentRequest(BaseModel):
    """An incident is always raised against a shipment the caller owns.

    There is no actor_id field: the actor is taken from the verified token.
    """

    model_config = ConfigDict(extra="forbid")

    shipment_id: str
    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    minutes_until_spoilage: Optional[float] = Field(None, ge=0)


class AdvanceIncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_state: IncidentState
    metadata: Optional[Dict[str, Any]] = None


def _incident_view(incident: Incident, shipment: Optional[Shipment] = None) -> dict:
    payload = {
        "id": incident.id,
        "shipmentId": incident.shipment_id,
        "state": incident.state.value,
        "assignedTruckId": incident.assigned_truck_id,
        "lat": incident.lat,
        "lng": incident.lng,
        "minutesUntilSpoilage": incident.minutes_until_spoilage,
        "createdAt": incident.created_at.isoformat() if incident.created_at else None,
        "updatedAt": incident.updated_at.isoformat() if incident.updated_at else None,
    }
    if shipment is not None:
        payload["cargoType"] = shipment.cargo_type
        payload["requiresRefrigeration"] = shipment.requires_refrigeration
        payload["requiredMaxTempC"] = shipment.required_max_temp_c
        payload["isHazmat"] = shipment.is_hazmat
    return payload


@router.post("/incidents", status_code=status.HTTP_201_CREATED)
async def create_new_incident(
    req: CreateIncidentRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    shipment = await db.get(Shipment, req.shipment_id)
    if not shipment or shipment.owner_company_id != principal.get("company_id"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Shipment not found or does not belong to your company",
        )

    incident = await create_incident(
        session=db,
        shipment_id=req.shipment_id,
        lat=req.lat,
        lng=req.lng,
        minutes_until_spoilage=req.minutes_until_spoilage,
        actor_id=actor_id_of(principal),
        event_publisher=event_producer.publish,
    )
    return _incident_view(incident, shipment)


@router.get("/incidents")
async def list_company_incidents(
    limit: int = 50,
    company_id: str = Depends(get_current_company),
    db: AsyncSession = Depends(get_db),
):
    """Operations feed scoped to the authenticated carrier company."""
    limit = min(max(limit, 1), 100)
    result = await db.execute(
        select(Incident, Shipment)
        .join(Shipment, Shipment.id == Incident.shipment_id)
        .where(Shipment.owner_company_id == company_id)
        .order_by(Incident.created_at.desc())
        .limit(limit)
    )
    rows = result.all()
    incidents = [_incident_view(incident, shipment) for incident, shipment in rows]

    # Attach each rescue's escrow in one query rather than one per incident.
    # The console shows where the money sits next to every open rescue, and
    # an N+1 here would be felt on a busy operations screen.
    from services.escrow_ledger.app.models import Escrow

    incident_ids = [i["id"] for i in incidents]
    if incident_ids:
        escrows = await db.execute(
            select(Escrow)
            .where(Escrow.incident_id.in_(incident_ids))
            .order_by(Escrow.created_at)
        )
        by_incident = {e.incident_id: e for e in escrows.scalars().all()}
        for payload in incidents:
            escrow = by_incident.get(payload["id"])
            if escrow is not None:
                payload["escrow"] = {
                    "id": escrow.id,
                    "state": escrow.state,
                    "amountInr": escrow.amount_inr,
                    "carrierPayoutInr": escrow.carrier_payout_inr,
                    "stateReason": escrow.state_reason,
                }

    return incidents


@router.get("/incidents/{id}")
async def get_incident_by_id(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    incident, shipment = await load_incident_for_owner(id, principal, db)
    payload = _incident_view(incident, shipment)
    escrow = await escrow_for_incident(db, incident.id)
    if escrow is not None:
        payload["escrow"] = {
            "id": escrow.id,
            "state": escrow.state,
            "amountInr": escrow.amount_inr,
            "carrierPayoutInr": escrow.carrier_payout_inr,
            "stateReason": escrow.state_reason,
        }
    return payload


@router.post("/incidents/{id}/advance")
async def advance_incident_state(
    id: str,
    req: AdvanceIncidentRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Move an incident to its next lifecycle state.

    RESCUE_ACCEPTED is deliberately not reachable here. Binding a rescue
    requires both the stranded owner and the helping carrier to agree, which
    is handled by the offer handshake -- a single call from one party must
    never be able to assign another company's truck to a job.
    """
    incident, shipment = await load_incident_for_owner(id, principal, db)

    if req.new_state == IncidentState.RESCUE_ACCEPTED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "RESCUE_ACCEPTED is reached by the two-way offer handshake, not by a "
                "direct state change. Use the rescue offer endpoints."
            ),
        )

    try:
        incident = await advance(
            session=db,
            incident_id=id,
            new_state=req.new_state,
            actor_id=actor_id_of(principal),
            metadata=req.metadata,
            event_publisher=event_producer.publish,
        )
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except IncidentNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    # The money follows the cargo. Leaving a dispatcher to move the escrow
    # separately is how the two records end up disagreeing.
    escrow = await sync_escrow_to_incident(
        db, incident, event_publisher=event_producer.publish
    )

    logger.info(
        "incident_advanced_via_api",
        incident_id=id,
        new_state=req.new_state.value,
        actor=actor_id_of(principal),
        escrow_state=escrow.state if escrow else None,
    )

    payload = _incident_view(incident, shipment)
    if escrow is not None:
        payload["escrow"] = {"id": escrow.id, "state": escrow.state}
    return payload


@router.get("/incidents/{id}/timeline")
async def get_timeline(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    await load_incident_for_owner(id, principal, db)
    events = await get_incident_timeline(db, id)
    return [
        {
            "id": e.id,
            "incidentId": e.incident_id,
            "type": e.type,
            "actorId": e.actor_id,
            "previousState": e.previous_state,
            "newState": e.new_state,
            "metadata": e.metadata_json,
            "createdAt": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/incidents/{id}/sla")
async def get_sla_metrics(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    await load_incident_for_owner(id, principal, db)
    events = await get_incident_timeline(db, id)
    return compute_sla([{"type": e.type, "created_at": e.created_at} for e in events])
