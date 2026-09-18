"""
Pricing Engine (Phase 4).
Isolated, versioned (v1.0), and deterministic pricing logic with urgency & scarcity premiums.
"""
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

CARGO_CLASS_RATE_PER_KM = {
    "standard": 40.0,
    "refrigerated": 65.0,
    "hazmat": 90.0,
}


@dataclass
class PriceBreakdown:
    base_rate_inr: float
    urgency_premium_inr: float
    scarcity_premium_inr: float
    total_inr: float
    formula_version: str = "v1.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_price(
    distance_km: float,
    cargo_class: str,
    hours_to_spoilage: Optional[float],
    num_compatible_nearby: int,
) -> PriceBreakdown:
    """
    Computes rescue pricing:
    - Base Rate = Distance (km) * Class Rate (INR/km)
    - Urgency Premium = Base Rate * max(0, (4 - hours_to_spoilage)/4) * 0.5 (if spoilage applicable)
    - Scarcity Premium = Base Rate * min(0.4, 1 / max(1, num_compatible_nearby)) (capped at 40%)
    - Total = Base Rate + Urgency Premium + Scarcity Premium
    """
    rate = CARGO_CLASS_RATE_PER_KM.get(cargo_class.lower(), 40.0)
    base_rate = distance_km * rate

    urgency_factor = (
        max(0.0, (4.0 - hours_to_spoilage) / 4.0) * 0.5
        if hours_to_spoilage is not None
        else 0.0
    )
    urgency_premium = base_rate * urgency_factor

    scarcity_factor = min(0.4, 1.0 / max(1, num_compatible_nearby))
    scarcity_premium = base_rate * scarcity_factor

    total = round(base_rate + urgency_premium + scarcity_premium, 2)
    return PriceBreakdown(
        base_rate_inr=round(base_rate, 2),
        urgency_premium_inr=round(urgency_premium, 2),
        scarcity_premium_inr=round(scarcity_premium, 2),
        total_inr=total,
        formula_version="v1.0",
    )
