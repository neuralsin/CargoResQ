"""
Pricing Engine Microservice API.
Includes Phase 19 RBAC on pricing override and Phase 18.3 OpenAPI docs.
"""
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, status
from pydantic import BaseModel, Field
from .pricing import calculate_price, PriceBreakdown
from shared.rbac import require_role, Role
from shared.observability import metrics_endpoint_response

app = FastAPI(
    title="CargoResQ Pricing Engine",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


class PricingRequest(BaseModel):
    distance_km: float = Field(..., gt=0, description="Road distance in kilometers")
    cargo_class: str = Field("standard", description="standard | refrigerated | hazmat")
    hours_to_spoilage: Optional[float] = Field(None, description="Hours remaining before cargo spoiled")
    num_compatible_nearby: int = Field(1, ge=0, description="Count of candidate trucks within radius")


class PriceOverrideRequest(BaseModel):
    incident_id: str
    proposed_total_inr: float = Field(..., gt=0)
    reason: str = Field(..., min_length=5)


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "pricing-engine", "version": "1.0.0"}


@app.get("/metrics")
async def metrics():
    return metrics_endpoint_response()


@app.post("/api/v1/pricing/calculate", response_model=PriceBreakdown)
async def get_price(req: PricingRequest):
    return calculate_price(
        distance_km=req.distance_km,
        cargo_class=req.cargo_class,
        hours_to_spoilage=req.hours_to_spoilage,
        num_compatible_nearby=req.num_compatible_nearby,
    )


@app.post("/api/v1/pricing/override")
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
