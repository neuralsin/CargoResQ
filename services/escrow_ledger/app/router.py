"""
Escrow Ledger APIRouter.
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from .escrow_service import (
    create_escrow,
    transition,
    verify_and_release,
    get_ledger_entries,
    IllegalTransition,
    EscrowNotFound,
)
from shared.database import get_db
from shared.rbac import require_role, Role

router = APIRouter(prefix="/api/v1/escrow", tags=["escrow"])


class CreateEscrowRequest(BaseModel):
    match_id: str
    amount_inr: float = Field(..., gt=0)


class TransitionRequest(BaseModel):
    new_state: str


class VerifyReleaseRequest(BaseModel):
    delivered_temp_log: List[float]
    required_max_temp: float


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_new_escrow(req: CreateEscrowRequest, db: AsyncSession = Depends(get_db)):
    escrow = await create_escrow(db, match_id=req.match_id, amount_inr=req.amount_inr)
    return {
        "id": escrow.id,
        "matchId": escrow.match_id,
        "amountInr": escrow.amount_inr,
        "state": escrow.state,
    }


@router.post("/{id}/transition")
async def transition_escrow(id: str, req: TransitionRequest, db: AsyncSession = Depends(get_db)):
    try:
        escrow = await transition(db, id, req.new_state)
        return {
            "id": escrow.id,
            "matchId": escrow.match_id,
            "amountInr": escrow.amount_inr,
            "state": escrow.state,
        }
    except IllegalTransition as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except EscrowNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Escrow not found")


@router.post("/{id}/release")
async def release_escrow(
    id: str,
    db: AsyncSession = Depends(get_db),
    role: Role = Depends(require_role(Role.FINANCE, Role.OPS_MANAGER)),
):
    """
    Phase 19 RBAC Check: Only FINANCE or OPS_MANAGER can manually authorize escrow release.
    """
    try:
        escrow = await transition(db, id, "RELEASED")
        return {
            "id": escrow.id,
            "state": escrow.state,
            "released_by_role": role.value,
            "message": "Escrow successfully released and ledger debited/credited.",
        }
    except (IllegalTransition, EscrowNotFound) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{id}/verify-and-release")
async def verify_temp_and_release(
    id: str,
    req: VerifyReleaseRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        escrow = await verify_and_release(
            db,
            escrow_id=id,
            delivered_temp_log=req.delivered_temp_log,
            required_max_temp=req.required_max_temp,
        )
        return {"id": escrow.id, "state": escrow.state}
    except (IllegalTransition, EscrowNotFound) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/{id}/ledger")
async def get_escrow_ledger(id: str, db: AsyncSession = Depends(get_db)):
    entries = await get_ledger_entries(db, id)
    return [
        {
            "id": e.id,
            "account": e.account,
            "debitInr": e.debit_inr,
            "creditInr": e.credit_inr,
            "createdAt": e.created_at.isoformat() if e.created_at else None,
        }
        for e in entries
    ]
