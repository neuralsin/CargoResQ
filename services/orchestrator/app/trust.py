"""
Lightweight Trust Signals (Phase 20).
Rule-based reputation & trust arithmetic based on rescue completion rates,
acceptance metrics, and dispute counts.
"""
from typing import Dict, Any, List


def compute_trust_score(company_stats: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes transparent company trust score out of 100:
    - Base: 100.0
    - Completed rescues bonus signal (>= 50)
    - Acceptance rate penalty if < 0.90
    - Dispute penalties: -5.0 per dispute
    """
    signals: List[str] = []
    score = 100.0

    completed = int(company_stats.get("completedRescues", 0))
    acceptance = float(company_stats.get("acceptanceRate", 1.0))
    disputes = int(company_stats.get("disputeCount", 0))

    if completed >= 50:
        signals.append(f"✓ {completed} completed rescues")
    elif completed > 0:
        signals.append(f"✓ {completed} rescue missions")

    if acceptance >= 0.9:
        signals.append(f"✓ {int(acceptance * 100)}% acceptance rate")
    else:
        penalty = (0.9 - acceptance) * 100.0
        score -= penalty
        signals.append(f"⚠ Acceptance rate below 90% (-{round(penalty, 1)})")

    if disputes == 0:
        signals.append("✓ 0 disputes")
    else:
        penalty = disputes * 5.0
        score -= penalty
        signals.append(f"⚠ {disputes} dispute(s) on record (-{round(penalty, 1)})")

    return {
        "trustScore": round(max(0.0, score), 1),
        "signals": signals,
    }
