"""
Rescue Score & Explainability (Phase 13).
Multi-factor weighted scoring algorithm producing both a quantitative ranking
and human-understandable explanation reasons.
"""
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List


@dataclass
class RescueScore:
    total: float
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_rescue_score(candidate: Dict[str, Any]) -> RescueScore:
    """
    Weights:
    - 35% ETA
    - 20% compatibility
    - 15% capacity
    - 10% reliability (company trust score)
    - 10% historical acceptance rate
    - 10% route alignment
    """
    max_eta = candidate.get("maxAcceptableEtaMinutes", 60.0)
    eta_val = candidate.get("etaMinutes", 30.0)
    eta_score = max(0.0, 100.0 - (eta_val / max(max_eta, 1.0)) * 100.0)

    compat_score = 100.0 if candidate.get("fullyCompatible", True) else 0.0

    req_vol = candidate.get("requiredVolumeM3", 1.0)
    avail_vol = candidate.get("availableVolumeM3", 5.0)
    capacity_score = min(100.0, (avail_vol / max(req_vol, 0.1)) * 50.0)

    reliability_score = float(candidate.get("companyTrustScore", 100.0))
    acceptance_score = float(candidate.get("historicalAcceptanceRate", 1.0)) * 100.0
    route_score = float(candidate.get("routeAlignmentScore", 0.5)) * 100.0

    total = (
        0.35 * eta_score
        + 0.20 * compat_score
        + 0.15 * capacity_score
        + 0.10 * reliability_score
        + 0.10 * acceptance_score
        + 0.10 * route_score
    )

    reasons = []
    if eta_val <= 20:
        reasons.append(f"✓ {round(eta_val, 1)} min ETA")
    if candidate.get("fullyCompatible", True):
        reasons.append("✓ Fully compatible (refrigeration/hazmat/size)")
    if capacity_score >= 40:
        reasons.append(f"✓ {round(avail_vol, 1)} m³ available")
    if acceptance_score >= 90:
        reasons.append(f"✓ {int(acceptance_score)}% successful rescues")
    if route_score >= 70:
        reasons.append("✓ Already travelling toward destination")

    return RescueScore(total=round(total, 1), reasons=reasons)
