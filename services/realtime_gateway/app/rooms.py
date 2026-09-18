"""
Room naming and server-side subscription authorisation.

A client may ask to subscribe to a room, but the server decides whether it
may. Room names are never trusted as supplied -- they are parsed, and the
principal is checked against the database where the room refers to a specific
record.
"""
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.models import Shipment

from .connection_manager import Principal


def company_room(company_id: str) -> str:
    """Everything a company's operators may see about their own business."""
    return f"company:{company_id}"


def driver_room(driver_id: str) -> str:
    """One driver's device."""
    return f"driver:{driver_id}"


def incident_room(incident_id: str) -> str:
    """Owner-side detail for one incident."""
    return f"incident:{incident_id}"


def offer_room(offer_id: str, carrier_company_id: str) -> str:
    """One carrier's view of one offer."""
    return f"offer:{offer_id}:carrier:{carrier_company_id}"


def sos_room(sos_id: str) -> str:
    """Participants in one SOS alert."""
    return f"sos:{sos_id}"


def network_room() -> str:
    """Cross-carrier broadcast channel.

    Everything delivered here is redacted by construction: it carries no
    price, no cargo value, no driver identity and no exact coordinates. It is
    how a nearby competitor learns that help is wanted at all.
    """
    return "network:broadcast"


def base_rooms(principal: Principal) -> List[str]:
    """Rooms a principal is subscribed to automatically on connect."""
    rooms = [company_room(principal.company_id), network_room()]
    if principal.is_driver:
        rooms.append(driver_room(principal.principal_id))
    return rooms


async def authorise_room(
    principal: Principal,
    room: str,
    db: AsyncSession,
) -> Optional[str]:
    """Return None if the subscription is allowed, else a refusal reason.

    Anything not explicitly recognised is refused. A room name the server does
    not understand is not a room the server should deliver to.
    """
    if room in base_rooms(principal):
        return None

    if room.startswith("company:"):
        return (
            None
            if room == company_room(principal.company_id)
            else "Not a member of that company"
        )

    if room.startswith("driver:"):
        if principal.is_driver and room == driver_room(principal.principal_id):
            return None
        return "Drivers may only subscribe to their own device channel"

    if room.startswith("incident:"):
        incident_id = room.split(":", 1)[1]
        return await _authorise_incident(principal, incident_id, db)

    if room.startswith("sos:"):
        # SOS participation is granted by the broadcast fan-out, which records
        # who was told. Until that table exists, only the reporting company's
        # own room carries SOS detail.
        return "SOS rooms are joined by invitation from the broadcast, not by request"

    return "Unknown room"


async def _authorise_incident(
    principal: Principal,
    incident_id: str,
    db: AsyncSession,
) -> Optional[str]:
    from services.orchestrator.app.models import Incident

    incident = await db.get(Incident, incident_id)
    if incident is None:
        return "Unknown room"

    result = await db.execute(select(Shipment).where(Shipment.id == incident.shipment_id))
    shipment = result.scalar_one_or_none()
    if shipment is None:
        return "Unknown room"
    if shipment.owner_company_id != principal.company_id:
        return "Unknown room"
    return None
