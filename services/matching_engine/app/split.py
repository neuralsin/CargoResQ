"""
Splitting a load across several trucks.

The matcher's usual question is "which single truck can take this?". For a
full artic of pharmaceuticals the honest answer is often none: the trucks that
are idle nearby are smaller rigid vans, and each one could take a third of the
pallets. Reporting "no rescue available" in that situation is technically true
and commercially absurd -- three vans are parked within fifteen kilometres of
a load that is about to be written off.

So when no one truck fits, the cargo is divided. The rules are deliberately
conservative, because splitting a consignment is a real operational cost:

  Every truck in a split must be independently compatible. A reefer load
  split across two reefers and one dry van is a spoiled load with extra
  paperwork. Capacity is the only constraint relaxed.

  Fewer trucks is better, then closer trucks. Each extra vehicle is another
  driver, another handover, another chance to lose a pallet, and another
  invoice -- so a two-way split always beats a three-way one even if the
  three-way is marginally nearer.

  A split is only offered when a single truck genuinely cannot do it. It is
  the fallback, never the preference.

The packing itself is first-fit-decreasing: sort by capacity, fill the biggest
trucks first. Optimal bin packing is NP-hard and the difference over a handful
of trucks is not worth the latency -- FFD is within a few percent and runs in
microseconds, which is the right trade when cargo is warming up.
"""
import math
from typing import Any, Dict, List, Optional

#: Nothing below this is worth sending a separate truck for. A leg carrying
#: 0.2 m3 costs a full callout to move almost nothing.
MIN_USEFUL_LEG_M3 = 0.5

#: More trucks than this stops being a rescue and becomes a removals job.
MAX_TRUCKS_IN_SPLIT = 4

#: Loading and securing part of a consignment, per leg, in minutes. A split
#: is slower than a straight transfer and the ETA has to say so.
TRANSFER_OVERHEAD_MINUTES_PER_LEG = 12.0


def plan_split(
    candidates: List[Dict[str, Any]],
    volume_m3: float,
    weight_kg: float,
    max_trucks: int = MAX_TRUCKS_IN_SPLIT,
) -> Optional[Dict[str, Any]]:
    """Divide a load across the fewest, nearest compatible trucks.

    `candidates` are trucks already filtered for temperature and hazmat
    compatibility but not for capacity. Returns None when the cargo cannot be
    covered even by every truck available, which is a real answer: the load
    needs equipment that is not on the network right now.
    """
    if volume_m3 <= 0 and weight_kg <= 0:
        return None

    usable = [
        c
        for c in candidates
        if (c.get("availableVolumeM3") or 0) > 0 and (c.get("availableWeightKg") or 0) > 0
    ]
    if len(usable) < 2:
        return None

    # Biggest first, nearest as the tie-break: capacity decides how many
    # trucks we need, distance decides which of the equals we pick.
    ordered = sorted(
        usable,
        key=lambda c: (-(c.get("availableVolumeM3") or 0), c.get("straightLineKm") or 0),
    )

    total_volume = sum(c["availableVolumeM3"] for c in ordered[:max_trucks])
    total_weight = sum(c["availableWeightKg"] for c in ordered[:max_trucks])
    if total_volume < volume_m3 or total_weight < weight_kg:
        return None

    legs: List[Dict[str, Any]] = []
    volume_left = float(volume_m3)
    weight_left = float(weight_kg)

    for truck in ordered:
        if volume_left <= 0.001 and weight_left <= 0.001:
            break
        if len(legs) >= max_trucks:
            break

        leg_volume = min(volume_left, truck["availableVolumeM3"])
        leg_weight = min(weight_left, truck["availableWeightKg"])

        # Keep volume and weight proportional to each other, so a leg is not
        # assigned 90% of the pallets and 10% of the tonnage.
        if volume_m3 > 0 and weight_kg > 0:
            volume_share = leg_volume / volume_m3
            weight_share = leg_weight / weight_kg
            share = min(volume_share, weight_share)
            leg_volume = round(volume_m3 * share, 2)
            leg_weight = round(weight_kg * share, 1)

        if leg_volume < MIN_USEFUL_LEG_M3 and volume_left > MIN_USEFUL_LEG_M3:
            continue

        legs.append(
            {
                "truckId": truck["truckId"],
                "companyId": truck["companyId"],
                "registrationNumber": truck.get("registrationNumber"),
                "latitude": truck.get("latitude"),
                "longitude": truck.get("longitude"),
                "distanceKm": truck.get("straightLineKm"),
                "refrigerated": truck.get("refrigerated"),
                "minTempC": truck.get("minTempC"),
                "volumeM3": leg_volume,
                "weightKg": leg_weight,
                "sharePct": round(
                    (leg_volume / volume_m3 * 100) if volume_m3 else 0.0, 1
                ),
            }
        )
        volume_left = round(volume_left - leg_volume, 3)
        weight_left = round(weight_left - leg_weight, 3)

    if volume_left > 0.01 or weight_left > 0.1:
        return None
    if len(legs) < 2:
        return None

    # Rounding can leave a sliver unassigned or over-assigned; push any
    # remainder onto the largest leg rather than shipping a plan that does not
    # add up to the consignment.
    assigned_volume = sum(leg["volumeM3"] for leg in legs)
    drift = round(volume_m3 - assigned_volume, 2)
    if abs(drift) >= 0.01:
        biggest = max(legs, key=lambda leg: leg["volumeM3"])
        biggest["volumeM3"] = round(biggest["volumeM3"] + drift, 2)
        for leg in legs:
            leg["sharePct"] = round(
                (leg["volumeM3"] / volume_m3 * 100) if volume_m3 else 0.0, 1
            )

    companies = {leg["companyId"] for leg in legs}
    slowest_km = max((leg["distanceKm"] or 0) for leg in legs)

    return {
        "legs": legs,
        "truckCount": len(legs),
        "companyCount": len(companies),
        "crossCarrier": len(companies) > 1,
        "totalVolumeM3": round(sum(leg["volumeM3"] for leg in legs), 2),
        "totalWeightKg": round(sum(leg["weightKg"] for leg in legs), 1),
        "furthestTruckKm": round(slowest_km, 1),
        "transferOverheadMinutes": round(
            TRANSFER_OVERHEAD_MINUTES_PER_LEG * len(legs), 1
        ),
        "notes": _notes(legs, companies),
    }


def _notes(legs: List[Dict[str, Any]], companies: set) -> List[str]:
    notes = [
        f"Cargo divided across {len(legs)} trucks "
        f"({', '.join(str(leg['registrationNumber']) for leg in legs)})"
    ]
    if len(companies) > 1:
        notes.append(
            f"{len(companies)} carriers involved -- each leg is priced and "
            "escrowed separately"
        )
    heaviest = max(legs, key=lambda leg: leg["volumeM3"])
    notes.append(
        f"Largest leg {heaviest['sharePct']:.0f}% on {heaviest['registrationNumber']}"
    )
    notes.append(
        f"Allow ~{TRANSFER_OVERHEAD_MINUTES_PER_LEG * len(legs):.0f} min extra "
        "for a multi-vehicle transfer"
    )
    return notes


def describe_shortfall(
    candidates: List[Dict[str, Any]], volume_m3: float, weight_kg: float
) -> Dict[str, Any]:
    """Why a split was not possible, in numbers an operator can act on."""
    available_volume = sum(c.get("availableVolumeM3") or 0 for c in candidates)
    available_weight = sum(c.get("availableWeightKg") or 0 for c in candidates)
    return {
        "trucksConsidered": len(candidates),
        "availableVolumeM3": round(available_volume, 1),
        "availableWeightKg": round(available_weight, 1),
        "requiredVolumeM3": volume_m3,
        "requiredWeightKg": weight_kg,
        "volumeShortfallM3": round(max(0.0, volume_m3 - available_volume), 1),
        "weightShortfallKg": round(max(0.0, weight_kg - available_weight), 1),
        "trucksWouldBeNeeded": (
            math.ceil(volume_m3 / max(c.get("availableVolumeM3") or 1 for c in candidates))
            if candidates
            else None
        ),
    }
