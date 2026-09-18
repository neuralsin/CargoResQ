"""
Incident Orchestrator APIRouter.
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from .models import IncidentState, Incident
from .orchestrator import (
    create_incident,
    advance,
    get_incident_timeline,
    IllegalTransition,
    IncidentNotFound,
)
from .sla import compute_sla
from .trust import compute_trust_score
from shared.database import get_db

router = APIRouter(prefix="/api/v1", tags=["orchestrator"])


class CreateIncidentRequest(BaseModel):
    shipment_id: str
    lat: float
    lng: float
    minutes_until_spoilage: Optional[float] = None
    actor_id: Optional[str] = "system"


class AdvanceIncidentRequest(BaseModel):
    new_state: IncidentState
    actor_id: str = "ops"
    metadata: Optional[Dict[str, Any]] = None


class TrustScoreRequest(BaseModel):
    completedRescues: int = Field(0, ge=0)
    acceptanceRate: float = Field(1.0, ge=0.0, le=1.0)
    disputeCount: int = Field(0, ge=0)


@router.post("/incidents", status_code=status.HTTP_201_CREATED)
async def create_new_incident(req: CreateIncidentRequest, db: AsyncSession = Depends(get_db)):
    incident = await create_incident(
        session=db,
        shipment_id=req.shipment_id,
        lat=req.lat,
        lng=req.lng,
        minutes_until_spoilage=req.minutes_until_spoilage,
        actor_id=req.actor_id,
    )
    return {
        "id": incident.id,
        "shipmentId": incident.shipment_id,
        "state": incident.state.value,
        "lat": incident.lat,
        "lng": incident.lng,
        "minutesUntilSpoilage": incident.minutes_until_spoilage,
        "createdAt": incident.created_at.isoformat() if incident.created_at else None,
    }


@router.post("/incidents/{id}/advance")
async def advance_incident_state(
    id: str, req: AdvanceIncidentRequest, db: AsyncSession = Depends(get_db)
):
    try:
        incident = await advance(
            session=db,
            incident_id=id,
            new_state=req.new_state,
            actor_id=req.actor_id,
            metadata=req.metadata,
        )
        return {
            "id": incident.id,
            "state": incident.state.value,
            "assignedTruckId": incident.assigned_truck_id,
            "updatedAt": incident.updated_at.isoformat() if incident.updated_at else None,
        }
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except IncidentNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")


@router.get("/incidents/{id}")
async def get_incident_by_id(id: str, db: AsyncSession = Depends(get_db)):
    incident = await db.get(Incident, id)
    if not incident:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return {
        "id": incident.id,
        "shipmentId": incident.shipment_id,
        "state": incident.state.value,
        "assignedTruckId": incident.assigned_truck_id,
        "lat": incident.lat,
        "lng": incident.lng,
        "minutesUntilSpoilage": incident.minutes_until_spoilage,
        "createdAt": incident.created_at.isoformat() if incident.created_at else None,
    }


@router.get("/incidents/{id}/timeline")
async def get_timeline(id: str, db: AsyncSession = Depends(get_db)):
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
async def get_sla_metrics(id: str, db: AsyncSession = Depends(get_db)):
    events = await get_incident_timeline(db, id)
    events_dicts = [
        {"type": e.type, "created_at": e.created_at}
        for e in events
    ]
    return compute_sla(events_dicts)


@router.post("/trust-score")
async def calculate_trust(stats: TrustScoreRequest):
    return compute_trust_score(stats.model_dump())
