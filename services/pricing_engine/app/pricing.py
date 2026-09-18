"""
Rescue pricing.

The brief: price on many factors, leave the helping carrier a genuinely
attractive margin, but never let the number become extortionate. Those pull
against each other, and the resolution is a formula that multiplies upward but
is then clamped from both ends:

  * a FLOOR, so a rescue is never worth less to the carrier than the cost of
    driving to it -- otherwise nobody sensible accepts;
  * a CEILING expressed two ways -- a multiple of what the same leg would cost
    as ordinary freight, and a share of the cargo's own value. A rescue that
    costs more than the cargo is worth is not a rescue, it is a hostage
    negotiation, and it is exactly what an unbounded urgency multiplier
    produces when a truck breaks down with four minutes of cold chain left.

Every quote records which clamp bound it, so a price can always be explained
after the fact.

The previous implementation took distance, cargo class, spoilage and candidate
count entirely from the client, persisted nothing, and had no bounds at all.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

FORMULA_VERSION = "v2.0"


@dataclass(frozen=True)
class PricingPolicy:
    """Every tunable in one place, so pricing is configuration, not folklore."""

    # Base freight rates, INR per km, by cargo class.
    rate_per_km: Dict[str, float] = field(
        default_factory=lambda: {
            "standard": 40.0,
            "refrigerated": 65.0,
            "hazmat": 90.0,
            "fragile": 55.0,
        }
    )
    #: Turning out at all costs something regardless of distance.
    callout_fee_inr: float = 900.0
    #: Transferring a load between two trailers is manual work.
    transfer_fee_per_tonne_inr: float = 220.0
    transfer_fee_per_m3_inr: float = 45.0

    #: Urgency multiplier ceiling. Reached as spoilage time approaches zero.
    max_urgency_multiplier: float = 1.85
    #: Spoilage horizon, in minutes, beyond which urgency adds nothing.
    urgency_horizon_minutes: float = 240.0

    #: Scarcity multiplier ceiling, when exactly one compatible truck exists.
    max_scarcity_multiplier: float = 1.40

    #: Reefer/hazmat handling premium.
    condition_multiplier: Dict[str, float] = field(
        default_factory=lambda: {"refrigerated": 1.15, "hazmat": 1.30, "fragile": 1.08}
    )

    #: A well-rated rescuer earns a little more; a poor one is discounted.
    #: Deliberately narrow -- reputation should nudge price, not set it.
    max_reputation_bonus: float = 0.08
    max_reputation_penalty: float = 0.10

    #: The carrier keeps this share; the platform takes the rest.
    platform_fee_pct: float = 0.12

    #: CEILINGS.
    #: Total may not exceed this multiple of ordinary freight for the same leg.
    max_multiple_of_normal_freight: float = 3.0
    #: Nor this share of the cargo's declared value.
    max_share_of_cargo_value: float = 0.25

    #: FLOOR. The carrier's payout must cover the run plus a real margin.
    carrier_margin_floor_pct: float = 0.18


DEFAULT_POLICY = PricingPolicy()


@dataclass
class PriceBreakdown:
    """A quote, and the arithmetic that produced it."""

    base_rate_inr: float
    callout_fee_inr: float
    transfer_fee_inr: float
    subtotal_inr: float

    urgency_multiplier: float
    scarcity_multiplier: float
    condition_multiplier: float
    reputation_multiplier: float

    uncapped_total_inr: float
    total_inr: float
    carrier_payout_inr: float
    platform_fee_inr: float

    normal_freight_inr: float
    cap_applied: Optional[str]
    floor_applied: bool
    factors: List[str]
    formula_version: str = FORMULA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def owner_view(self) -> Dict[str, Any]:
        """What the cargo owner sees: the full arithmetic, because they pay it."""
        return self.to_dict()

    def carrier_view(self) -> Dict[str, Any]:
        """What the helping carrier sees: what they earn, and why the job is what it is.

        Not the owner's total, and not the platform's margin. A carrier needs
        enough to decide whether the run is worth making -- the payout, the
        distance and the conditions -- and nothing about what the cargo is
        worth to its owner.
        """
        return {
            "payoutInr": self.carrier_payout_inr,
            "currency": "INR",
            "factors": [f for f in self.factors if "value" not in f.lower()],
            "formulaVersion": self.formula_version,
        }


def _cargo_class(requires_refrigeration: bool, is_hazmat: bool, is_fragile: bool = False) -> str:
    if is_hazmat:
        return "hazmat"
    if requires_refrigeration:
        return "refrigerated"
    if is_fragile:
        return "fragile"
    return "standard"


def _urgency_multiplier(minutes_until_spoilage: Optional[float], policy: PricingPolicy) -> float:
    """Rises as the cargo's remaining life shortens, and stops rising.

    Capped rather than asymptotic: an uncapped urgency term means the price of
    help approaches infinity exactly when the owner is least able to refuse it.
    """
    if minutes_until_spoilage is None:
        return 1.0
    remaining = max(0.0, min(minutes_until_spoilage, policy.urgency_horizon_minutes))
    pressure = 1.0 - (remaining / policy.urgency_horizon_minutes)
    return 1.0 + pressure * (policy.max_urgency_multiplier - 1.0)


def _scarcity_multiplier(num_compatible_nearby: int, policy: PricingPolicy) -> float:
    """Rises when few compatible trucks exist, and stops rising."""
    n = max(1, int(num_compatible_nearby))
    if n >= 6:
        return 1.0
    scarcity = (6 - n) / 5.0
    return 1.0 + scarcity * (policy.max_scarcity_multiplier - 1.0)


def _reputation_multiplier(trust_score: Optional[float], policy: PricingPolicy) -> float:
    """A narrow adjustment for a proven -- or unproven -- rescuer.

    An unrated carrier sits at 1.0 rather than being penalised: charging less
    for an unknown quantity would be a discount for anonymity, and charging
    more would make it impossible for a new entrant to win work.
    """
    if trust_score is None:
        return 1.0
    score = max(0.0, min(100.0, trust_score))
    if score >= 80.0:
        fraction = (score - 80.0) / 20.0
        return 1.0 + fraction * policy.max_reputation_bonus
    fraction = (80.0 - score) / 80.0
    return 1.0 - fraction * policy.max_reputation_penalty


def calculate_price(
    distance_km: float,
    requires_refrigeration: bool = False,
    is_hazmat: bool = False,
    is_fragile: bool = False,
    weight_kg: float = 0.0,
    volume_m3: float = 0.0,
    minutes_until_spoilage: Optional[float] = None,
    num_compatible_nearby: int = 1,
    cargo_value_inr: Optional[float] = None,
    carrier_trust_score: Optional[float] = None,
    policy: PricingPolicy = DEFAULT_POLICY,
) -> PriceBreakdown:
    """Price one rescue.

    Every argument is a fact the server already holds about the shipment, the
    candidate and the situation -- none of it is taken from a client.
    """
    distance_km = max(0.0, float(distance_km))
    cargo_class = _cargo_class(requires_refrigeration, is_hazmat, is_fragile)
    rate = policy.rate_per_km.get(cargo_class, policy.rate_per_km["standard"])

    base_rate = distance_km * rate
    transfer_fee = (
        (max(0.0, weight_kg) / 1000.0) * policy.transfer_fee_per_tonne_inr
        + max(0.0, volume_m3) * policy.transfer_fee_per_m3_inr
    )
    subtotal = base_rate + policy.callout_fee_inr + transfer_fee

    urgency = _urgency_multiplier(minutes_until_spoilage, policy)
    scarcity = _scarcity_multiplier(num_compatible_nearby, policy)
    condition = policy.condition_multiplier.get(cargo_class, 1.0)
    reputation = _reputation_multiplier(carrier_trust_score, policy)

    uncapped = subtotal * urgency * scarcity * condition * reputation

    # What this leg would cost as an ordinary, pre-booked freight movement.
    # The ceiling is expressed relative to this so it scales with the job.
    normal_freight = max(
        distance_km * policy.rate_per_km["standard"] + policy.callout_fee_inr, 1.0
    )

    total = uncapped
    cap_applied: Optional[str] = None

    freight_cap = normal_freight * policy.max_multiple_of_normal_freight
    if total > freight_cap:
        total = freight_cap
        cap_applied = "multiple_of_normal_freight"

    if cargo_value_inr and cargo_value_inr > 0:
        value_cap = cargo_value_inr * policy.max_share_of_cargo_value
        if total > value_cap:
            total = value_cap
            cap_applied = "share_of_cargo_value"

    # The floor is applied last, so it wins over the ceiling. If the two
    # genuinely conflict the job is not economic, and pricing it below the
    # carrier's cost would just mean nobody accepts.
    floor = subtotal * (1.0 + policy.carrier_margin_floor_pct)
    floor_applied = False
    if total < floor:
        total = floor
        floor_applied = True
        cap_applied = None

    total = round(total, 2)
    carrier_payout = round(total * (1.0 - policy.platform_fee_pct), 2)
    platform_fee = round(total - carrier_payout, 2)

    factors: List[str] = [f"{distance_km:.1f} km at INR {rate:.0f}/km ({cargo_class})"]
    if policy.callout_fee_inr:
        factors.append(f"Callout INR {policy.callout_fee_inr:.0f}")
    if transfer_fee > 0:
        factors.append(f"Cargo transfer INR {transfer_fee:.0f}")
    if urgency > 1.0:
        factors.append(f"Urgency x{urgency:.2f}")
    if scarcity > 1.0:
        factors.append(f"Only {num_compatible_nearby} compatible nearby: x{scarcity:.2f}")
    if condition > 1.0:
        factors.append(f"{cargo_class.title()} handling x{condition:.2f}")
    if reputation != 1.0:
        direction = "premium" if reputation > 1 else "discount"
        factors.append(f"Rescuer reputation {direction} x{reputation:.2f}")
    if cap_applied == "multiple_of_normal_freight":
        factors.append(
            f"Capped at {policy.max_multiple_of_normal_freight:.1f}x normal freight"
        )
    elif cap_applied == "share_of_cargo_value":
        factors.append(
            f"Capped at {policy.max_share_of_cargo_value:.0%} of cargo value"
        )
    if floor_applied:
        factors.append("Raised to the carrier's minimum viable margin")

    return PriceBreakdown(
        base_rate_inr=round(base_rate, 2),
        callout_fee_inr=round(policy.callout_fee_inr, 2),
        transfer_fee_inr=round(transfer_fee, 2),
        subtotal_inr=round(subtotal, 2),
        urgency_multiplier=round(urgency, 4),
        scarcity_multiplier=round(scarcity, 4),
        condition_multiplier=round(condition, 4),
        reputation_multiplier=round(reputation, 4),
        uncapped_total_inr=round(uncapped, 2),
        total_inr=total,
        carrier_payout_inr=carrier_payout,
        platform_fee_inr=platform_fee,
        normal_freight_inr=round(normal_freight, 2),
        cap_applied=cap_applied,
        floor_applied=floor_applied,
        factors=factors,
    )
