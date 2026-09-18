import pytest
from services.matching_engine.app.scoring import calculate_rescue_score
from services.matching_engine.app.spoilage import classify_safety_margin
from services.matching_engine.app.routing import haversine_distance_km, get_road_eta


def test_rescue_score_differentiation():
    # Fast, fully compatible truck with high trust
    good_candidate = {
        "etaMinutes": 15,
        "maxAcceptableEtaMinutes": 60,
        "fullyCompatible": True,
        "requiredVolumeM3": 3.0,
        "availableVolumeM3": 8.0,
        "companyTrustScore": 98.0,
        "historicalAcceptanceRate": 0.96,
        "routeAlignmentScore": 0.9,
    }
    score_good = calculate_rescue_score(good_candidate)

    # Slow, incompatible truck with lower trust
    poor_candidate = {
        "etaMinutes": 55,
        "maxAcceptableEtaMinutes": 60,
        "fullyCompatible": False,
        "requiredVolumeM3": 3.0,
        "availableVolumeM3": 1.0,
        "companyTrustScore": 45.0,
        "historicalAcceptanceRate": 0.15,
        "routeAlignmentScore": 0.2,
    }
    score_poor = calculate_rescue_score(poor_candidate)

    assert score_good.total > score_poor.total
    assert len(score_good.reasons) > 0
    # Reasons must reflect the actual figures. They used to be emitted from
    # hardcoded inputs, so every candidate claimed a 95% success rate.
    assert any("min away" in r for r in score_good.reasons)
    assert any("Trust score 98" in r for r in score_good.reasons)
    assert any("Accepts 96%" in r for r in score_good.reasons)
    assert any("heading toward" in r for r in score_good.reasons)

    # The weaker candidate is described as weak, not flattered.
    assert any("Rarely accepts" in r or "Low trust" in r for r in score_poor.reasons)
    assert not any("Trust score 9" in r for r in score_poor.reasons)


def test_unknown_carrier_scores_neutral_not_perfect():
    """A carrier we know nothing about must not score like a proven one."""
    known = calculate_rescue_score({
        "etaMinutes": 15, "maxAcceptableEtaMinutes": 60, "fullyCompatible": True,
        "requiredVolumeM3": 3.0, "availableVolumeM3": 8.0,
        "companyTrustScore": 98.0, "historicalAcceptanceRate": 0.96,
        "routeAlignmentScore": 0.9,
    })
    # Same truck, same distance, but no history at all -- defaults apply.
    unknown = calculate_rescue_score({
        "etaMinutes": 15, "maxAcceptableEtaMinutes": 60, "fullyCompatible": True,
        "requiredVolumeM3": 3.0, "availableVolumeM3": 8.0,
    })
    assert unknown.total < known.total


def test_spoilage_margin_classification_boundaries():
    # > 30 margin -> SAFE
    safe = classify_safety_margin(eta_to_rescue_minutes=20, minutes_until_spoilage=60)
    assert safe.status == "SAFE"
    assert safe.margin_minutes == 40.0

    # Exactly 30 margin -> WARNING
    boundary_30 = classify_safety_margin(eta_to_rescue_minutes=30, minutes_until_spoilage=60)
    assert boundary_30.status == "WARNING"

    # Between 10 and 30 -> WARNING
    warning = classify_safety_margin(eta_to_rescue_minutes=31, minutes_until_spoilage=47)
    assert warning.status == "WARNING"
    assert warning.margin_minutes == 16.0

    # Exactly 10 margin -> WARNING
    boundary_10 = classify_safety_margin(eta_to_rescue_minutes=30, minutes_until_spoilage=40)
    assert boundary_10.status == "WARNING"

    # Between 0 and 10 -> CRITICAL
    critical = classify_safety_margin(eta_to_rescue_minutes=35, minutes_until_spoilage=40)
    assert critical.status == "CRITICAL"
    assert critical.margin_minutes == 5.0

    # Negative margin -> LOST
    lost = classify_safety_margin(eta_to_rescue_minutes=45, minutes_until_spoilage=40)
    assert lost.status == "LOST"
    assert lost.margin_minutes == -5.0


@pytest.mark.asyncio
async def test_routing_haversine_and_eta():
    # Bangalore (12.9716, 77.5946) to Chennai (13.0827, 80.2707) approx 290-350 km
    dist = haversine_distance_km(12.9716, 77.5946, 13.0827, 80.2707)
    assert 270 < dist < 320

    res = await get_road_eta((12.9716, 77.5946), (13.0827, 80.2707))
    assert res["distanceKm"] > 250
    assert res["etaMinutes"] > 100
