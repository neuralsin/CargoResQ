"""
Escrow Ledger Microservice API.
Includes Phase 19 RBAC on escrow release and Phase 18.3 OpenAPI docs.
"""
from typing import List, Optional
from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .models import Base
from .escrow_service import (
    create_escrow,
    transition,
    verify_and_release,
    get_ledger_entries,
    IllegalTransition,
    EscrowNotFound,
)
from shared.rbac import require_role, Role
from shared.observability import metrics_endpoint_response
import os

DATABASE_URL = os.getenv("ESCROW_DATABASE_URL", "sqlite+aiosqlite:///escrow.db")
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

app = FastAPI(
    title="CargoResQ Escrow & Ledger Service",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


async def get_db():
    async with async_session() as session:
        yield session


@app.on_event("startup")
async def on_startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


class CreateEscrowRequest(BaseModel):
    match_id: str
    amount_inr: float = Field(..., gt=0)


class TransitionRequest(BaseModel):
    new_state: str


class VerifyReleaseRequest(BaseModel):
    delivered_temp_log: List[float]
    required_max_temp: float


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "escrow-ledger", "version": "1.0.0"}


@app.get("/metrics")
async def metrics():
    return metrics_endpoint_response()


@app.post("/api/v1/escrow", status_code=status.HTTP_201_CREATED)
async def create_new_escrow(req: CreateEscrowRequest, db: AsyncSession = Depends(get_db)):
    escrow = await create_escrow(db, match_id=req.match_id, amount_inr=req.amount_inr)
    return {
        "id": escrow.id,
        "matchId": escrow.match_id,
        "amountInr": escrow.amount_inr,
        "state": escrow.state,
    }


@app.post("/api/v1/escrow/{id}/transition")
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


@app.post("/api/v1/escrow/{id}/release")
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


@app.post("/api/v1/escrow/{id}/verify-and-release")
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


@app.get("/api/v1/escrow/{id}/ledger")
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
