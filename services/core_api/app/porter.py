"""
Porter-style On-Demand Fleet Categories & Pricing Estimator.
Intra-city & Inter-city instant freight estimation with Cold-Chain Reefer support.
"""
from typing import Dict, Any, List
from pydantic import BaseModel, Field

VEHICLE_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "tata_ace": {
        "id": "tata_ace",
        "name": "Tata Ace (Chota Hathi)",
        "tagline": "Ideal for small medical parcels & dry freight",
        "maxWeightKg": 750.0,
        "maxVolumeM3": 2.5,
        "isRefrigerated": False,
        "baseFareInr": 250.0,
        "baseKm": 2.0,
        "perKmRate": 25.0,
        "loadingAssistanceFee": 150.0,
        "rating": 4.92,
    },
    "bolero_maxi": {
        "id": "bolero_maxi",
        "name": "Bolero Maxi Truck",
        "tagline": "Medium bulk consignments & hospital supplies",
        "maxWeightKg": 1200.0,
        "maxVolumeM3": 4.5,
        "isRefrigerated": False,
        "baseFareInr": 400.0,
        "baseKm": 2.0,
        "perKmRate": 32.0,
        "loadingAssistanceFee": 200.0,
        "rating": 4.88,
    },
    "tata_407": {
        "id": "tata_407",
        "name": "Tata 407 (10ft Closed Body)",
        "tagline": "Industrial freight & secure locked transit",
        "maxWeightKg": 2500.0,
        "maxVolumeM3": 8.5,
        "isRefrigerated": False,
        "baseFareInr": 750.0,
        "baseKm": 3.0,
        "perKmRate": 45.0,
        "loadingAssistanceFee": 300.0,
        "rating": 4.95,
    },
    "tata_ultra_reefer": {
        "id": "tata_ultra_reefer",
        "name": "Tata Ultra Express Reefer Unit",
        "tagline": "Sub-zero insulin, vaccines & bio-pharma (-20°C to +8°C)",
        "maxWeightKg": 3500.0,
        "maxVolumeM3": 12.0,
        "isRefrigerated": True,
        "minTempC": -20.0,
        "baseFareInr": 1800.0,
        "baseKm": 5.0,
        "perKmRate": 65.0,
        "telemetryFee": 250.0,
        "loadingAssistanceFee": 350.0,
        "rating": 4.98,
    },
    "eicher_pharma_reefer": {
        "id": "eicher_pharma_reefer",
        "name": "Eicher 19ft Heavy Pharma Reefer",
        "tagline": "High-volume cross-carrier emergency cold-chain rescue",
        "maxWeightKg": 7000.0,
        "maxVolumeM3": 24.0,
        "isRefrigerated": True,
        "minTempC": -25.0,
        "baseFareInr": 3200.0,
        "baseKm": 5.0,
        "perKmRate": 85.0,
        "telemetryFee": 350.0,
        "loadingAssistanceFee": 500.0,
        "rating": 4.99,
    },
}


def estimate_porter_fare(
    vehicle_type: str,
    distance_km: float,
    requires_loading_help: bool = False,
    is_urgent_rescue: bool = False,
) -> Dict[str, Any]:
    if vehicle_type not in VEHICLE_CATEGORIES:
        raise ValueError(f"Unknown vehicle '{vehicle_type}'. Valid: {list(VEHICLE_CATEGORIES.keys())}")

    spec = VEHICLE_CATEGORIES[vehicle_type]
    base = spec["baseFareInr"]
    base_km = spec["baseKm"]
    per_km = spec["perKmRate"]

    extra_km = max(0.0, distance_km - base_km)
    km_charge = extra_km * per_km
    loading_charge = spec["loadingAssistanceFee"] if requires_loading_help else 0.0
    telemetry_charge = spec.get("telemetryFee", 0.0)

    subtotal = base + km_charge + loading_charge + telemetry_charge
    urgency_premium = subtotal * 0.25 if is_urgent_rescue else 0.0
    total = round(subtotal + urgency_premium, 2)

    return {
        "vehicleType": vehicle_type,
        "vehicleName": spec["name"],
        "distanceKm": distance_km,
        "baseFare": base,
        "distanceCharge": round(km_charge, 2),
        "loadingFee": loading_charge,
        "telemetryFee": telemetry_charge,
        "urgencyPremium": round(urgency_premium, 2),
        "estimatedTotalInr": total,
        "estimatedPickupEtaMinutes": 14 if is_urgent_rescue else 25,
    }
