"""
Event projections: deciding who sees what.

Every backend event used to be bridged straight onto a global broadcast, so
the default was "tell everyone". That default is inverted here: an event with
no registered projector is dropped and logged. A new event added anywhere in
the codebase is therefore invisible on the wire until somebody deliberately
writes down its audience.

Each projector returns explicit (room, payload) pairs, and different audiences
get *different payloads built from different key sets* -- not one payload with
fields filtered out. A leak then requires adding a key to the wrong builder,
which is a visible act in review, rather than forgetting to add a filter.
"""
from typing import Any, Awaitable, Callable, Dict, List, NamedTuple, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from shared.observability import logger

from .rooms import company_room, incident_room, network_room


class Delivery(NamedTuple):
    room: str
    payload: Dict[str, Any]


Projector = Callable[[Dict[str, Any], Optional[AsyncSession]], Awaitable[List[Delivery]]]

#: Keys that must never appear in a payload delivered outside the cargo
#: owner's own company room. Enforced by test_ws_redaction.
OWNER_ONLY_KEYS = frozenset(
    {
        "valueInr",
        "value_inr",
        "priceTotalInr",
        "price_total_inr",
        "amountInr",
        "amount_inr",
        "platformFeeInr",
        "platform_fee_inr",
        "priceBreakdown",
        "price_breakdown_json",
        "hashed_password",
        "vitalsJson",
        "vitals_json",
        "driverMedicalSnapshot",
        "conditionNote",
    }
)


def _envelope(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": event_type, "payload": payload}


def _payload_of(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Events arrive wrapped in an EventEnvelope; unwrap defensively."""
    inner = envelope.get("payload")
    return inner if isinstance(inner, dict) else envelope


# ---------------------------------------------------------------------------
# Projectors
# ---------------------------------------------------------------------------


async def project_breakdown_detected(
    envelope: Dict[str, Any], db: Optional[AsyncSession]
) -> List[Delivery]:
    """A breakdown is full detail to its owner, and a bare hint to the network.

    The network form deliberately omits cargo value, cargo type, the company
    and the exact position -- a competitor learns that help is wanted nearby,
    not what the load is worth.
    """
    p = _payload_of(envelope)
    company_id = p.get("companyId")
    incident_id = p.get("incidentId")

    deliveries: List[Delivery] = []
    if company_id:
        deliveries.append(
            Delivery(
                company_room(company_id),
                _envelope(
                    "breakdown.detected",
                    {
                        "incidentId": incident_id,
                        "shipmentId": p.get("shipmentId"),
                        "cargoType": p.get("cargoType"),
                        "lat": p.get("lat"),
                        "lng": p.get("lng"),
                        "hoursToSpoilage": p.get("hoursToSpoilage"),
                        "requiresRefrigeration": p.get("requiresRefrigeration"),
                        "isHazmat": p.get("isHazmat"),
                        "valueInr": p.get("valueInr"),
                    },
                ),
            )
        )
    if incident_id:
        deliveries.append(
            Delivery(
                network_room(),
                _envelope(
                    "network.assistance_wanted",
                    {
                        "incidentId": incident_id,
                        "approxLat": _coarse(p.get("lat")),
                        "approxLng": _coarse(p.get("lng")),
                        "locationPrecision": "~1km",
                        "requiresRefrigeration": p.get("requiresRefrigeration"),
                        "isHazmat": p.get("isHazmat"),
                        "volumeM3": p.get("volumeM3"),
                        "weightKg": p.get("weightKg"),
                    },
                ),
            )
        )
    return deliveries


async def project_incident_state(
    envelope: Dict[str, Any], db: Optional[AsyncSession]
) -> List[Delivery]:
    """Lifecycle changes go to the owner's company and the incident room."""
    p = _payload_of(envelope)
    incident_id = p.get("incidentId")
    if not incident_id:
        return []

    body = _envelope(
        str(envelope.get("eventType", "incident.updated")),
        {
            "incidentId": incident_id,
            "previousState": p.get("previousState"),
            "newState": p.get("newState"),
            "assignedTruckId": p.get("assignedTruckId"),
        },
    )
    deliveries = [Delivery(incident_room(incident_id), body)]

    company_id = await _owner_company_for_incident(incident_id, db)
    if company_id:
        deliveries.append(Delivery(company_room(company_id), body))
    return deliveries


async def project_escrow_state(
    envelope: Dict[str, Any], db: Optional[AsyncSession]
) -> List[Delivery]:
    """Escrow movement reaches both parties, but with different detail.

    The owner sees the state of the money they committed. The rescuing carrier
    sees that the escrow moved, without the owner's total or the platform fee.
    """
    p = _payload_of(envelope)
    escrow_id = p.get("escrowId")
    if not escrow_id:
        return []

    owner_id = p.get("ownerCompanyId")
    carrier_id = p.get("carrierCompanyId")
    state = p.get("state")

    deliveries: List[Delivery] = []
    if owner_id:
        deliveries.append(
            Delivery(
                company_room(owner_id),
                _envelope(
                    "escrow.state_changed",
                    {
                        "escrowId": escrow_id,
                        "incidentId": p.get("incidentId"),
                        "state": state,
                        "role": "owner",
                    },
                ),
            )
        )
    if carrier_id and carrier_id != owner_id:
        deliveries.append(
            Delivery(
                company_room(carrier_id),
                _envelope(
                    "escrow.state_changed",
                    {
                        "escrowId": escrow_id,
                        "incidentId": p.get("incidentId"),
                        "state": state,
                        "role": "carrier",
                    },
                ),
            )
        )
    return deliveries


def _coarse(value: Optional[float], places: int = 2) -> Optional[float]:
    """Round a coordinate down to roughly a kilometre of precision."""
    if value is None:
        return None
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


async def _owner_company_for_incident(
    incident_id: str, db: Optional[AsyncSession]
) -> Optional[str]:
    if db is None:
        return None
    from sqlalchemy import select

    from services.core_api.app.models import Shipment
    from services.orchestrator.app.models import Incident

    result = await db.execute(
        select(Shipment.owner_company_id)
        .join(Incident, Incident.shipment_id == Shipment.id)
        .where(Incident.id == incident_id)
    )
    row = result.first()
    return row[0] if row else None


async def project_offer_event(
    envelope: Dict[str, Any], db: Optional[AsyncSession]
) -> List[Delivery]:
    """An offer event, projected differently for each side.

    The event payload carries no money at all -- the owner's price is read
    from the offer row when building the owner's view. A price therefore
    cannot reach a carrier's channel by accident, because it is never in the
    envelope that the carrier's projection is built from.
    """
    p = _payload_of(envelope)
    offer_id = p.get("offerId")
    owner_id = p.get("ownerCompanyId")
    carrier_id = p.get("carrierCompanyId")
    if not offer_id:
        return []

    event_type = str(envelope.get("eventType", "offer.updated"))
    shared = {
        "offerId": offer_id,
        "incidentId": p.get("incidentId"),
        "state": p.get("state"),
        "etaMinutes": p.get("etaMinutes"),
        "distanceKm": p.get("distanceKm"),
        "expiresAt": p.get("expiresAt"),
    }

    deliveries: List[Delivery] = []
    if owner_id:
        owner_body = dict(shared)
        owner_body["carrierCompanyId"] = carrier_id
        if db is not None:
            price = await _offer_price(offer_id, db)
            if price is not None:
                owner_body.update(price)
        deliveries.append(Delivery(company_room(owner_id), _envelope(event_type, owner_body)))

    if carrier_id:
        carrier_body = dict(shared)
        carrier_body["truckId"] = p.get("carrierTruckId")
        if db is not None:
            payout = await _offer_payout(offer_id, db)
            if payout is not None:
                carrier_body["payoutInr"] = payout
        deliveries.append(
            Delivery(company_room(carrier_id), _envelope(event_type, carrier_body))
        )

        driver_id = p.get("carrierDriverId")
        if driver_id:
            from .rooms import driver_room

            driver_body = dict(shared)
            driver_body["truckId"] = p.get("carrierTruckId")
            deliveries.append(
                Delivery(driver_room(str(driver_id)), _envelope(event_type, driver_body))
            )

    return deliveries


async def _offer_price(offer_id: str, db: AsyncSession) -> Optional[Dict[str, Any]]:
    from services.orchestrator.app.offer_models import RescueOffer

    offer = await db.get(RescueOffer, offer_id)
    if offer is None:
        return None
    return {
        "priceTotalInr": offer.price_total_inr,
        "carrierPayoutInr": offer.carrier_payout_inr,
        "platformFeeInr": offer.platform_fee_inr,
    }


async def _offer_payout(offer_id: str, db: AsyncSession) -> Optional[float]:
    from services.orchestrator.app.offer_models import RescueOffer

    offer = await db.get(RescueOffer, offer_id)
    return offer.carrier_payout_inr if offer is not None else None


async def project_telemetry_alert(
    envelope: Dict[str, Any], db: Optional[AsyncSession]
) -> List[Delivery]:
    """A derived alert reaches only the company operating the truck.

    Nobody else needs to know that a competitor's reefer has been stationary
    for an hour, and the occurrence count is included so the console can
    distinguish a brief pause from a long one.
    """
    p = _payload_of(envelope)
    company_id = p.get("companyId")
    if not company_id:
        return []
    return [
        Delivery(
            company_room(company_id),
            _envelope(
                "telemetry.alert",
                {
                    "alertId": p.get("alertId"),
                    "truckId": p.get("truckId"),
                    "shipmentId": p.get("shipmentId"),
                    "alertType": p.get("alertType"),
                    "severity": p.get("severity"),
                    "occurrenceCount": p.get("occurrenceCount"),
                    "detail": p.get("detail"),
                },
            ),
        )
    ]


async def project_sos(
    envelope: Dict[str, Any], db: Optional[AsyncSession]
) -> List[Delivery]:
    """An SOS reaches its own company in full, and told carriers in redacted form.

    Recipients are the companies the fan-out decided to tell, computed and
    recorded server-side at raise time -- never derived on the read path and
    never open to every connected socket.
    """
    p = _payload_of(envelope)
    sos_id = p.get("sosId")
    company_id = p.get("companyId")
    if not sos_id or not company_id:
        return []

    event_type = str(envelope.get("eventType", "sos.updated"))

    own = {
        "sosId": sos_id,
        "category": p.get("category"),
        "severity": p.get("severity"),
        "status": p.get("status"),
        "ackCount": p.get("ackCount"),
        "isOwnDriver": True,
    }
    deliveries = [Delivery(company_room(company_id), _envelope(event_type, own))]

    # Nearby carriers: what kind of help is wanted and roughly where, with no
    # driver identity, no cargo and no condition detail.
    network_body = {
        "sosId": sos_id,
        "category": p.get("category"),
        "severity": p.get("severity"),
        "status": p.get("status"),
        "approxLat": p.get("approxLat"),
        "approxLng": p.get("approxLng"),
        "locationPrecision": "~1km",
        "assistanceNeeded": p.get("assistanceNeeded"),
        "isOwnDriver": False,
    }
    for recipient in p.get("networkRecipients") or []:
        deliveries.append(
            Delivery(company_room(str(recipient)), _envelope(event_type, network_body))
        )

    return deliveries


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROJECTIONS: Dict[str, Projector] = {
    "breakdown.detected": project_breakdown_detected,
    "escrow.state_changed": project_escrow_state,
}

# Every incident.* lifecycle event shares one projector.
_INCIDENT_EVENTS = [
    "incident.breakdown_reported",
    "incident.triaging",
    "incident.matching",
    "incident.rescue_offered",
    "incident.rescue_accepted",
    "incident.driver_en_route",
    "incident.cargo_transfer",
    "incident.rescue_in_transit",
    "incident.delivered",
    "incident.verification",
    "incident.escrow_released",
    "incident.disputed",
    "incident.cancelled",
]
for _name in _INCIDENT_EVENTS:
    PROJECTIONS[_name] = project_incident_state

_OFFER_EVENTS = [
    "offer.created",
    "offer.carrier_accepted",
    "offer.owner_confirmed",
    "offer.bound",
    "offer.declined",
    "offer.withdrawn",
    "offer.cancelled",
    "offer.expired",
]
for _name in _OFFER_EVENTS:
    PROJECTIONS[_name] = project_offer_event

PROJECTIONS["telemetry.alert"] = project_telemetry_alert

_SOS_EVENTS = [
    "sos.raised",
    "sos.acknowledged",
    "sos.responder_update",
    "sos.status_changed",
    "sos.escalated",
    "sos.duress_suspected",
]
for _name in _SOS_EVENTS:
    PROJECTIONS[_name] = project_sos


async def project(
    topic: str, envelope: Dict[str, Any], db: Optional[AsyncSession] = None
) -> List[Delivery]:
    """Resolve an event to its explicit audience.

    An unregistered topic is dropped, not broadcast. This is the whole point:
    the safe default is silence.
    """
    projector = PROJECTIONS.get(topic)
    if projector is None:
        logger.debug("ws_event_dropped_no_projection", topic=topic)
        return []
    try:
        return await projector(envelope, db)
    except Exception as exc:  # pragma: no cover - a projector must never break the bus
        logger.error("ws_projection_failed", topic=topic, error=str(exc))
        return []
