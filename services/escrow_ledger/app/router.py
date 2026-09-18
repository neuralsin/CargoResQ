"""
Escrow ledger API.

Every route here moves or reveals money, so every route is authenticated and
scoped to a party of the escrow. The previous version left create, transition,
verify-and-release and ledger-read completely open, which made the RBAC guard
on /release decorative -- an anonymous caller could reach RELEASED through
/transition and post the ledger entries without a token.

Escrows are not created over HTTP any more. They are created by the rescue
handshake when both parties bind, so that an escrow always references a real
incident and two real companies.
"""
from typing import Any, Dict, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from services.core_api.app.auth import actor_id_of, get_current_principal
from services.core_api.app.events.producer import event_producer
from services.core_api.app.models import Shipment
from shared.database import get_db
from shared.observability import logger
from shared.rbac import ESCROW_AUTHORISING_ROLES

from .escrow_service import (
    EscrowNotFound,
    IllegalTransition,
    get_ledger_entries,
    ledger_balance,
    transition,
    verify_and_release,
)
from .models import Escrow

router = APIRouter(prefix="/api/v1/escrow", tags=["escrow"])


class TransitionRequest(BaseModel):
    new_state: str = Field(..., description="Target state in the escrow lifecycle")
    reason: str | None = Field(None, max_length=256)


async def _load_escrow_for_party(
    escrow_id: str,
    principal: Dict[str, Any],
    db: AsyncSession,
) -> Tuple[Escrow, str]:
    """Return (escrow, role_of_caller) where role is 'owner' or 'carrier'.

    A caller who is neither party gets 404 rather than 403: whether a given
    escrow id exists is itself commercial information about other carriers.
    """
    escrow = await db.get(Escrow, escrow_id)
    if not escrow:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escrow not found")

    company_id = principal.get("company_id")
    if escrow.owner_company_id and company_id == escrow.owner_company_id:
        return escrow, "owner"
    if escrow.carrier_company_id and company_id == escrow.carrier_company_id:
        return escrow, "carrier"

    logger.warn(
        "escrow_access_denied",
        escrow_id=escrow_id,
        company_id=company_id,
    )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escrow not found")


def _escrow_view(escrow: Escrow, viewer: str) -> dict:
    """Project an escrow for the viewing party.

    The cargo owner sees the full amount they are paying. The rescuing carrier
    sees only their own payout -- the owner's total and the platform's margin
    are not theirs to see.
    """
    base = {
        "id": escrow.id,
        "incidentId": escrow.incident_id,
        "state": escrow.state,
        "stateReason": escrow.state_reason,
        "currency": escrow.currency,
        "createdAt": escrow.created_at.isoformat() if escrow.created_at else None,
        "updatedAt": escrow.updated_at.isoformat() if escrow.updated_at else None,
    }
    if viewer == "owner":
        base["amountInr"] = escrow.amount_inr
        base["carrierPayoutInr"] = escrow.carrier_payout_inr
        base["carrierCompanyId"] = escrow.carrier_company_id
    else:
        base["payoutInr"] = escrow.carrier_payout_inr
    return base


@router.get("/{id}")
async def get_escrow(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    escrow, viewer = await _load_escrow_for_party(id, principal, db)
    return _escrow_view(escrow, viewer)


@router.post("/{id}/transition")
async def transition_escrow(
    id: str,
    req: TransitionRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Advance an escrow through its lifecycle.

    Terminal money-moving states are not reachable here. RELEASED requires the
    verification endpoint (which reads recorded evidence), and DISPUTED is
    raised by verification or by an explicit dispute. Allowing either to be set
    directly is what made the authorisation on /release meaningless.
    """
    escrow, viewer = await _load_escrow_for_party(id, principal, db)

    if req.new_state in {"RELEASED", "DISPUTED"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "RELEASED and DISPUTED are settled by POST /api/v1/escrow/{id}/verify, "
                "which evaluates the recorded condition log. They cannot be set directly."
            ),
        )
    if viewer != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the cargo owner may advance the escrow lifecycle",
        )

    try:
        escrow = await transition(
            db,
            id,
            req.new_state,
            reason=req.reason,
            event_publisher=event_producer.publish,
        )
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except EscrowNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escrow not found")

    logger.info(
        "escrow_transitioned",
        escrow_id=id,
        new_state=req.new_state,
        actor=actor_id_of(principal),
    )
    return _escrow_view(escrow, viewer)


@router.post("/{id}/verify")
async def verify_escrow(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """Settle an escrow against the condition log recorded by the platform.

    The caller supplies nothing. The temperature limit is read from the
    shipment and the readings are read from what the driver's device actually
    reported. Previously both came from the request body, so the party being
    paid could assert their own evidence.
    """
    escrow, viewer = await _load_escrow_for_party(id, principal, db)

    caller_role = principal.get("role")
    if caller_role not in {r.value for r in ESCROW_AUTHORISING_ROLES}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Settling an escrow requires one of: "
                f"{[r.value for r in ESCROW_AUTHORISING_ROLES]}"
            ),
        )
    if viewer != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the cargo owner may settle the escrow",
        )

    required_max_temp = None
    readings: list[float] = []
    if escrow.incident_id:
        from services.orchestrator.app.models import Incident

        incident = await db.get(Incident, escrow.incident_id)
        if incident:
            shipment = await db.get(Shipment, incident.shipment_id)
            if shipment:
                required_max_temp = shipment.required_max_temp_c
                readings = await _recorded_temperatures(db, shipment.id)

    try:
        escrow, verdict = await verify_and_release(
            db,
            escrow_id=id,
            delivered_temp_log=readings,
            required_max_temp=required_max_temp,
            event_publisher=event_producer.publish,
        )
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except EscrowNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escrow not found")

    # The incident is the operational record; it has to end up saying what
    # the ledger concluded rather than sitting at VERIFICATION forever.
    from services.orchestrator.app.dispatch import record_settlement_on_incident

    incident = await record_settlement_on_incident(
        db, escrow, actor_id_of(principal), event_producer.publish
    )

    payload = _escrow_view(escrow, viewer)
    payload["verification"] = verdict.to_dict()
    if incident is not None:
        payload["incidentState"] = incident.state.value
    return payload


async def _recorded_temperatures(db: AsyncSession, shipment_id: str) -> list[float]:
    """Read the condition log the platform recorded for a shipment.

    Returns an empty list until telemetry ingest lands, which makes
    verification report INSUFFICIENT_DATA rather than releasing funds on no
    evidence -- the correct behaviour for an unverifiable shipment.
    """
    try:
        from services.telemetry.app.models import CargoReading  # noqa: F401
    except ImportError:
        return []

    from sqlalchemy import select

    from services.telemetry.app.models import CargoReading

    result = await db.execute(
        select(CargoReading.temperature_c)
        .where(CargoReading.shipment_id == shipment_id)
        .where(CargoReading.temperature_c.is_not(None))
        .order_by(CargoReading.recorded_at)
    )
    return [row[0] for row in result.all()]


@router.get("/{id}/ledger")
async def get_escrow_ledger(
    id: str,
    principal: Dict[str, Any] = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    escrow, viewer = await _load_escrow_for_party(id, principal, db)
    entries = await get_ledger_entries(db, id)
    balance = await ledger_balance(db, id)

    # The platform-fee line reveals the owner/carrier price split, so the
    # carrier sees only the postings that concern their own payout.
    if viewer == "carrier":
        entries = [e for e in entries if e.account != "platform_fee_income"]
        balance = {"escrowId": id, "entryCount": len(entries)}

    return {
        "balance": balance,
        "entries": [
            {
                "id": e.id,
                "postingRef": e.posting_ref,
                "postingType": e.posting_type,
                "account": e.account,
                "debitInr": e.debit_inr,
                "creditInr": e.credit_inr,
                "memo": e.memo,
                "createdAt": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entries
        ],
    }
