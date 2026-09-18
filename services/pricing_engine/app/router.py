"""
Pricing API.

A quote is an estimate here, not a commitment: the binding price for a rescue
is the one recorded on the offer, computed server-side from the shipment and
the chosen candidate. This endpoint exists so an operator can sanity-check
what a job would cost before raising one.

The override endpoint is gone. It accepted a new total, echoed it back, and
persisted nothing -- an authorisation check guarding an operation that did not
happen. If manual pricing is wanted it belongs on the offer, where it can be
recorded against the quote it replaced.
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from services.core_api.app.auth import get_current_principal

from .pricing import DEFAULT_POLICY, calculate_price

router = APIRouter(prefix="/api/v1/pricing", tags=["pricing"])


class PricingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    distance_km: float = Field(..., gt=0, description="Road distance in kilometres")
    requires_refrigeration: bool = False
    is_hazmat: bool = False
    weight_kg: float = Field(0.0, ge=0)
    volume_m3: float = Field(0.0, ge=0)
    minutes_until_spoilage: Optional[float] = Field(
        None, ge=0, description="Time remaining before the cargo is compromised"
    )
    num_compatible_nearby: int = Field(
        1, ge=0, description="Compatible trucks within the search radius"
    )
    cargo_value_inr: Optional[float] = Field(None, ge=0)
    carrier_trust_score: Optional[float] = Field(None, ge=0, le=100)


@router.post("/quote")
async def quote(
    req: PricingRequest,
    principal: Dict[str, Any] = Depends(get_current_principal),
):
    """Estimate what a rescue with these parameters would cost."""
    breakdown = calculate_price(
        distance_km=req.distance_km,
        requires_refrigeration=req.requires_refrigeration,
        is_hazmat=req.is_hazmat,
        weight_kg=req.weight_kg,
        volume_m3=req.volume_m3,
        minutes_until_spoilage=req.minutes_until_spoilage,
        num_compatible_nearby=req.num_compatible_nearby,
        cargo_value_inr=req.cargo_value_inr,
        carrier_trust_score=req.carrier_trust_score,
    )
    return breakdown.owner_view()


@router.get("/policy")
async def pricing_policy(principal: Dict[str, Any] = Depends(get_current_principal)):
    """The coefficients and bounds currently in force.

    Published deliberately. A carrier deciding whether to join a network, and
    an owner deciding whether to trust its prices, are both entitled to know
    that urgency is capped rather than open-ended.
    """
    policy = DEFAULT_POLICY
    return {
        "formulaVersion": "v2.0",
        "ratePerKmInr": policy.rate_per_km,
        "calloutFeeInr": policy.callout_fee_inr,
        "transferFeePerTonneInr": policy.transfer_fee_per_tonne_inr,
        "transferFeePerM3Inr": policy.transfer_fee_per_m3_inr,
        "maxUrgencyMultiplier": policy.max_urgency_multiplier,
        "maxScarcityMultiplier": policy.max_scarcity_multiplier,
        "conditionMultiplier": policy.condition_multiplier,
        "platformFeePct": policy.platform_fee_pct,
        "caps": {
            "maxMultipleOfNormalFreight": policy.max_multiple_of_normal_freight,
            "maxShareOfCargoValue": policy.max_share_of_cargo_value,
        },
        "carrierMarginFloorPct": policy.carrier_margin_floor_pct,
        "note": (
            "Urgency and scarcity raise the price but are bounded: a rescue "
            "never exceeds a fixed multiple of ordinary freight for the same "
            "leg, nor a fixed share of the cargo's declared value."
        ),
    }
