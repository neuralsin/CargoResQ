"""
Pricing Engine APIRouter.
"""
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from .pricing import calculate_price, PriceBreakdown
from shared.rbac import require_role, Role

router = APIRouter(prefix="/api/v1/pricing", tags=["pricing"])


class PricingRequest(BaseModel):
    distance_km: float = Field(..., gt=0, description="Road distance in kilometers")
    cargo_class: str = Field("standard", description="standard | refrigerated | hazmat")
    hours_to_spoilage: Optional[float] = Field(None, description="Hours remaining before cargo spoiled")
    num_compatible_nearby: int = Field(1, ge=0, description="Count of candidate trucks within radius")


class PriceOverrideRequest(BaseModel):
    incident_id: str
    proposed_total_inr: float = Field(..., gt=0)
    reason: str = Field(..., min_length=5)


@router.post("/calculate", response_model=PriceBreakdown)
async def get_price(req: PricingRequest):
    return calculate_price(
        distance_km=req.distance_km,
        cargo_class=req.cargo_class,
        hours_to_spoilage=req.hours_to_spoilage,
        num_compatible_nearby=req.num_compatible_nearby,
    )


@router.post("/override")
async def override_pricing(
    req: PriceOverrideRequest,
    role: Role = Depends(require_role(Role.OPS_MANAGER)),
):
    """Phase 19: Only OPS_MANAGER can manually override algorithmic pricing."""
    return {
        "status": "overridden",
        "incident_id": req.incident_id,
        "new_total_inr": req.proposed_total_inr,
        "authorized_by": role.value,
        "reason": req.reason,
    }
