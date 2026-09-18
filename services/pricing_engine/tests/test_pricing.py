import pytest
from services.pricing_engine.app.pricing import calculate_price


def test_standard_cargo_no_urgency():
    p = calculate_price(
        distance_km=10,
        cargo_class="standard",
        hours_to_spoilage=None,
        num_compatible_nearby=5,
    )
    assert p.base_rate_inr == 400.0
    assert p.urgency_premium_inr == 0.0
    assert p.formula_version == "v1.0"


def test_high_urgency_increases_price():
    low_urgency = calculate_price(
        10, "refrigerated", hours_to_spoilage=3.9, num_compatible_nearby=5
    )
    high_urgency = calculate_price(
        10, "refrigerated", hours_to_spoilage=0.5, num_compatible_nearby=5
    )
    assert high_urgency.total_inr > low_urgency.total_inr


def test_scarcity_capped_at_40_percent():
    p = calculate_price(
        10, "standard", hours_to_spoilage=None, num_compatible_nearby=1
    )
    assert p.scarcity_premium_inr == pytest.approx(p.base_rate_inr * 0.4)


def test_hazmat_rate_per_km():
    p = calculate_price(
        distance_km=20,
        cargo_class="hazmat",
        hours_to_spoilage=None,
        num_compatible_nearby=10,
    )
    # 20 km * 90 INR/km = 1800 base rate
    assert p.base_rate_inr == 1800.0
