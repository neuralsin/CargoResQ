"""
Ownership guards for incident-scoped routes.

Incidents are reachable only through the company that owns the underlying
shipment. `incidents.shipment_id` carries no foreign key yet, so ownership is
resolved by an explicit join rather than a relationship traversal.
"""
from typing import Any, Dict, Tuple

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.auth import get_current_principal
from services.core_api.app.models import Shipment
from shared.database import get_db

from .models import Incident


async def load_incident_for_owner(
    incident_id: str,
    principal: Dict[str, Any],
    db: AsyncSession,
) -> Tuple[Incident, Shipment]:
    """Return (incident, shipment) iff the principal's company owns the cargo.

    A third party gets 404 rather than 403: confirming that an incident id
    exists is itself a disclosure about another carrier's operations.
    """
    result = await db.execute(
        select(Incident, Shipment)
        .join(Shipment, Shipment.id == Incident.shipment_id)
        .where(Incident.id == incident_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    incident, shipment = row
    if shipment.owner_company_id != principal.get("company_id"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return incident, shipment


async def require_incident_owner(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> Tuple[Incident, Shipment, Dict[str, Any]]:
    """FastAPI dependency for routes with an `{id}` incident path parameter."""
    incident, shipment = await load_incident_for_owner(id, principal, db)
    return incident, shipment, principal
