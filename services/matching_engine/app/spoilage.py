"""
ETA-to-Spoilage Intelligence (Phase 14).
Classifies safety margins for cold-chain and perishable cargo rescue missions.
"""
from dataclasses import dataclass, asdict
from typing import Dict, Any


@dataclass
class SafetyMargin:
    eta_minutes: float
    safe_window_minutes: float
    margin_minutes: float
    status: str  # SAFE | WARNING | CRITICAL | LOST

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_safety_margin(
    eta_to_rescue_minutes: float,
    minutes_until_spoilage: float,
) -> SafetyMargin:
    """
    Classifies urgency based on time remaining before cargo spoilage:
    - SAFE: margin > 30 mins
    - WARNING: 10 <= margin <= 30 mins
    - CRITICAL: 0 <= margin < 10 mins
    - LOST: margin < 0 mins (cargo already compromised)
    """
    margin = minutes_until_spoilage - eta_to_rescue_minutes
    if margin > 30.0:
        status = "SAFE"
    elif margin >= 10.0:
        status = "WARNING"
    elif margin >= 0.0:
        status = "CRITICAL"
    else:
        status = "LOST"

    return SafetyMargin(
        eta_minutes=round(eta_to_rescue_minutes, 1),
        safe_window_minutes=round(minutes_until_spoilage, 1),
        margin_minutes=round(margin, 1),
        status=status,
    )
