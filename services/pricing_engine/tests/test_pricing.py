"""
Pricing: the multipliers, and the bounds that keep them honest.

The requirement pulls in two directions -- leave the helping carrier a
genuinely attractive margin, but never let the price become extortionate. The
bounds are what reconcile them, so most of these tests are about the clamps
rather than the arithmetic.
"""
import pytest

from services.pricing_engine.app.pricing import (
    DEFAULT_POLICY,
    PricingPolicy,
    calculate_price,
)


def test_base_rate_reflects_distance_and_cargo_class():
    standard = calculate_price(distance_km=10)
    reefer = calculate_price(distance_km=10, requires_refrigeration=True)
    hazmat = calculate_price(distance_km=10, is_hazmat=True)

    assert standard.base_rate_inr == 400.0   # 10 km at INR 40
    assert reefer.base_rate_inr == 650.0     # 10 km at INR 65
    assert hazmat.base_rate_inr == 900.0     # 10 km at INR 90


def test_urgency_raises_the_price_but_is_capped():
    relaxed = calculate_price(distance_km=40, minutes_until_spoilage=240)
    tight = calculate_price(distance_km=40, minutes_until_spoilage=30)
    desperate = calculate_price(distance_km=40, minutes_until_spoilage=0)

    assert tight.urgency_multiplier > relaxed.urgency_multiplier
    assert desperate.urgency_multiplier > tight.urgency_multiplier
    # The important property: it stops. An unbounded urgency term means the
    # price of help approaches infinity exactly when the owner cannot refuse.
    assert desperate.urgency_multiplier == DEFAULT_POLICY.max_urgency_multiplier


def test_no_spoilage_deadline_means_no_urgency_premium():
    assert calculate_price(distance_km=40).urgency_multiplier == 1.0


def test_scarcity_raises_the_price_but_is_capped():
    plentiful = calculate_price(distance_km=40, num_compatible_nearby=10)
    scarce = calculate_price(distance_km=40, num_compatible_nearby=2)
    sole = calculate_price(distance_km=40, num_compatible_nearby=1)

    assert plentiful.scarcity_multiplier == 1.0
    assert scarce.scarcity_multiplier > 1.0
    assert sole.scarcity_multiplier == DEFAULT_POLICY.max_scarcity_multiplier


def test_price_never_exceeds_a_multiple_of_ordinary_freight():
    """The ceiling that stops a rescue becoming a hostage negotiation."""
    worst_case = calculate_price(
        distance_km=40,
        requires_refrigeration=True,
        minutes_until_spoilage=0,
        num_compatible_nearby=1,
        weight_kg=3000,
        volume_m3=12,
        cargo_value_inr=10_000_000,
    )
    assert worst_case.uncapped_total_inr > worst_case.total_inr, "the cap must bind"
    assert worst_case.cap_applied == "multiple_of_normal_freight"
    assert worst_case.total_inr <= (
        worst_case.normal_freight_inr * DEFAULT_POLICY.max_multiple_of_normal_freight + 0.01
    )


def test_price_never_exceeds_a_share_of_what_the_cargo_is_worth():
    """Charging more than the cargo is worth is not a rescue."""
    cheap_cargo = calculate_price(
        distance_km=40,
        requires_refrigeration=True,
        minutes_until_spoilage=5,
        num_compatible_nearby=1,
        cargo_value_inr=20_000,
    )
    assert cheap_cargo.cap_applied == "share_of_cargo_value"
    assert cheap_cargo.total_inr <= 20_000 * DEFAULT_POLICY.max_share_of_cargo_value + 0.01


def test_the_floor_protects_the_carrier_from_an_uneconomic_job():
    """The floor wins over the ceiling.

    If the two genuinely conflict the job is not economic, and pricing it
    below the carrier's cost just means nobody accepts -- which helps nobody.
    """
    tiny_value = calculate_price(distance_km=120, cargo_value_inr=500)
    assert tiny_value.floor_applied
    assert tiny_value.cap_applied is None
    assert tiny_value.total_inr >= tiny_value.subtotal_inr


def test_carrier_keeps_the_large_majority_of_the_price():
    """The helping company should do well out of this."""
    for kwargs in (
        {"distance_km": 20},
        {"distance_km": 200, "is_hazmat": True},
        {"distance_km": 45, "minutes_until_spoilage": 15, "num_compatible_nearby": 1},
    ):
        price = calculate_price(cargo_value_inr=900_000, **kwargs)
        share = price.carrier_payout_inr / price.total_inr
        assert share == pytest.approx(1 - DEFAULT_POLICY.platform_fee_pct, abs=0.005)
        assert share >= 0.85


def test_payout_and_fee_always_reconstruct_the_total():
    price = calculate_price(distance_km=63, requires_refrigeration=True, weight_kg=1200)
    assert price.carrier_payout_inr + price.platform_fee_inr == pytest.approx(
        price.total_inr, abs=0.01
    )


def test_reputation_nudges_the_price_without_setting_it():
    trusted = calculate_price(distance_km=40, carrier_trust_score=100)
    unknown = calculate_price(distance_km=40, carrier_trust_score=None)
    poor = calculate_price(distance_km=40, carrier_trust_score=10)

    assert trusted.reputation_multiplier > unknown.reputation_multiplier > poor.reputation_multiplier
    # An unrated carrier is neither penalised nor discounted: charging less for
    # anonymity rewards it, and charging more locks out every new entrant.
    assert unknown.reputation_multiplier == 1.0
    # The adjustment stays small -- reputation should not dominate the price.
    assert trusted.reputation_multiplier <= 1 + DEFAULT_POLICY.max_reputation_bonus
    assert poor.reputation_multiplier >= 1 - DEFAULT_POLICY.max_reputation_penalty


def test_breakdown_explains_which_bound_applied():
    capped = calculate_price(
        distance_km=40, minutes_until_spoilage=0, num_compatible_nearby=1,
        requires_refrigeration=True, cargo_value_inr=10_000_000,
    )
    assert any("Capped" in f for f in capped.factors)
    assert capped.formula_version == "v2.0"


def test_policy_is_configurable():
    """Coefficients are configuration, not folklore buried in the function."""
    generous = PricingPolicy(platform_fee_pct=0.05)
    price = calculate_price(distance_km=40, policy=generous)
    assert price.carrier_payout_inr / price.total_inr == pytest.approx(0.95, abs=0.005)


def test_carrier_view_hides_the_owners_total():
    price = calculate_price(distance_km=40, cargo_value_inr=500_000)
    view = price.carrier_view()
    assert view["payoutInr"] == price.carrier_payout_inr
    assert "total_inr" not in view
    assert "platform_fee_inr" not in view
    assert "uncapped_total_inr" not in view
