"""
Seed an enterprise-grade demonstration dataset.

Run explicitly -- never on boot:
    python scripts/seed_demo.py                 # generated passwords, printed once
    python scripts/seed_demo.py --password X    # fixed password, for a scripted demo
    python scripts/seed_demo.py --reset         # delete existing demo rows first

Creates 4 carriers, 9 trucks, 5 drivers, 5 shipments, active breakdown incidents
with countdown clocks and compatible rescue offers, live telemetry states,
cold-chain temperature trajectory, alerts, SOS emergencies, and balanced
double-entry escrow ledger postings.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
import secrets
import string
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select

from services.core_api.app.auth import hash_password
from services.core_api.app.models import (
    Company,
    Driver,
    Shipment,
    StorageFacility,
    Truck,
    TruckStatus,
)
from services.escrow_ledger.app.models import Escrow, LedgerEntry
from services.orchestrator.app.models import Incident, IncidentEvent, IncidentState
from services.orchestrator.app.offer_models import OfferState, RescueOffer
from services.orchestrator.app.reputation_models import CompanyReputation, RescueRating
from services.pricing_engine.app.pricing import calculate_price
from services.sos.app.models import (
    SosAlert,
    SosBroadcast,
    SosResponse,
    SosCategory,
    SosSeverity,
    SosStatus,
)
from services.telemetry.app.models import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    CargoReading,
    TelemetryAlert,
    TelemetryPing,
    TruckLiveState,
)
from shared.database import async_session
from shared.schema import ensure_schema
import shared.models_registry  # noqa: F401


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Demo-" + "".join(secrets.choice(alphabet) for _ in range(12))


DEMO_COMPANY_IDS = (
    "comp_apex_pharma",
    "comp_northline",
    "comp_metro_swift",
    "comp_eagle_haul",
)



def build_track_history(
    trucks: list[tuple[str, str, str, float, float, float, float]],
    now: "datetime",
    minutes: int = 40,
    interval_seconds: int = 30,
) -> list["TelemetryPing"]:
    """Lay a plausible breadcrumb trail behind each truck.

    Positions are walked backwards from where the truck is now, along the
    reverse of its current heading, at a distance consistent with its speed.
    A parked truck gets a tight cluster with a little GPS jitter rather than a
    single point, which is what a stationary phone actually produces.
    """
    import math
    import uuid as _uuid

    pings: list[TelemetryPing] = []
    steps = max(1, (minutes * 60) // interval_seconds)

    for truck_id, company_id, driver_id, lat, lng, speed_kph, heading in trucks:
        # Metres covered between two fixes at this speed.
        step_m = (speed_kph * 1000.0 / 3600.0) * interval_seconds
        back_bearing = math.radians((heading + 180.0) % 360.0)

        for index in range(steps, 0, -1):
            distance_m = step_m * index
            # Rough local projection: fine over the few km a trail covers.
            d_lat = (distance_m * math.cos(back_bearing)) / 111_320.0
            d_lng = (distance_m * math.sin(back_bearing)) / (
                111_320.0 * max(math.cos(math.radians(lat)), 0.01)
            )
            # A stationary phone still wanders a few metres between fixes.
            jitter = 0.00004 if speed_kph < 1 else 0.0
            recorded = now - timedelta(seconds=interval_seconds * index)

            pings.append(
                TelemetryPing(
                    truck_id=truck_id,
                    driver_id=driver_id,
                    company_id=company_id,
                    device_id="seed-" + truck_id,
                    client_ping_id=_uuid.uuid4().hex[:16],
                    recorded_at=recorded,
                    received_at=recorded,
                    latitude=lat + d_lat + (jitter if index % 2 else -jitter),
                    longitude=lng + d_lng,
                    accuracy_m=8.0 if speed_kph > 1 else 14.0,
                    speed_kph=speed_kph if speed_kph > 1 else 0.0,
                    heading_deg=heading if speed_kph > 1 else None,
                    battery_pct=max(35.0, 95.0 - index * 0.4),
                    is_charging=speed_kph > 1,
                    network_type="4g",
                    provider="fused",
                )
            )

    return pings


async def seed(password: str | None, reset: bool) -> list[tuple[str, str, str]]:
    await ensure_schema()
    credentials: list[tuple[str, str, str]] = []

    async with async_session() as session:
        if reset:
            # Delete dependent entities in clean dependency order
            await session.execute(delete(StorageFacility))
            await session.execute(delete(RescueRating))
            await session.execute(delete(CompanyReputation))
            await session.execute(delete(SosResponse))
            await session.execute(delete(SosBroadcast))
            await session.execute(delete(SosAlert))
            await session.execute(delete(TelemetryAlert))
            await session.execute(delete(CargoReading))
            await session.execute(delete(TelemetryPing))
            await session.execute(delete(TruckLiveState))
            await session.execute(delete(LedgerEntry))
            await session.execute(delete(Escrow))
            await session.execute(delete(RescueOffer))
            await session.execute(delete(IncidentEvent))
            await session.execute(delete(Incident))
            await session.execute(delete(Shipment))
            await session.execute(delete(Driver))
            await session.execute(delete(Truck))
            for cid in DEMO_COMPANY_IDS:
                company = await session.get(Company, cid)
                if company:
                    await session.delete(company)
            await session.commit()

        existing = await session.execute(
            select(Company).where(Company.id.in_(DEMO_COMPANY_IDS))
        )
        if existing.scalars().first():
            print("Demo companies already exist. Re-run with --reset to recreate them.")
            return []

        apex_pw = password or generate_password()
        north_pw = password or generate_password()
        metro_pw = password or generate_password()
        eagle_pw = password or generate_password()
        driver_pw = password or generate_password()

        now = datetime.now(timezone.utc)

        # ------------------------------------------------------------------
        # 1. Companies
        # ------------------------------------------------------------------
        apex = Company(
            id="comp_apex_pharma",
            name="Apex Cold Logistics",
            email="ops@apexcold.example",
            hashed_password=hash_password(apex_pw),
            trust_score=94.0,
        )
        northline = Company(
            id="comp_northline",
            name="Northline Freight",
            email="ops@northline.example",
            hashed_password=hash_password(north_pw),
            trust_score=89.0,
        )
        metro = Company(
            id="comp_metro_swift",
            name="Metro Swift Logistics",
            email="ops@metroswift.example",
            hashed_password=hash_password(metro_pw),
            trust_score=91.0,
        )
        eagle = Company(
            id="comp_eagle_haul",
            name="Eagle Heavy Haulage",
            email="ops@eaglehaul.example",
            hashed_password=hash_password(eagle_pw),
            trust_score=96.0,
        )
        session.add_all([apex, northline, metro, eagle])
        await session.flush()

        # ------------------------------------------------------------------
        # 2. Trucks
        # ------------------------------------------------------------------
        # Apex fleet
        trk_apex_9042 = Truck(
            id="trk_apex_9042",
            company_id=apex.id,
            registration_number="MH-12-TX-9042",
            latitude=18.5204,
            longitude=73.8567,
            status=TruckStatus.in_transit,
            refrigerated=True,
            min_temp_c=-20.0,
            max_volume_m3=12.0,
            max_weight_kg=3500.0,
        )
        trk_apex_5510 = Truck(
            id="trk_apex_5510",
            company_id=apex.id,
            registration_number="MH-12-TX-5510",
            latitude=18.7500,
            longitude=73.4200,
            status=TruckStatus.in_transit,
            refrigerated=True,
            min_temp_c=-25.0,
            max_volume_m3=18.0,
            max_weight_kg=5500.0,
        )
        trk_apex_2201 = Truck(
            id="trk_apex_2201",
            company_id=apex.id,
            registration_number="MH-12-CR-2201",
            latitude=18.7200,
            longitude=73.8100,
            status=TruckStatus.idle,
            refrigerated=True,
            min_temp_c=-35.0,
            max_volume_m3=15.0,
            max_weight_kg=4500.0,
        )
        trk_apex_7740 = Truck(
            id="trk_apex_7740",
            company_id=apex.id,
            registration_number="MH-12-VN-7740",
            latitude=18.5100,
            longitude=73.9200,
            status=TruckStatus.idle,
            refrigerated=False,
            max_volume_m3=22.0,
            max_weight_kg=7500.0,
        )

        # Northline fleet (rescuer counterparty)
        trk_north_412 = Truck(
            id="trk_north_412",
            company_id=northline.id,
            registration_number="MH-14-EQ-0412",
            latitude=18.5850,
            longitude=73.7400,
            status=TruckStatus.idle,
            refrigerated=True,
            min_temp_c=-25.0,
            max_volume_m3=24.0,
            max_weight_kg=7000.0,
        )
        trk_north_880 = Truck(
            id="trk_north_880",
            company_id=northline.id,
            registration_number="MH-14-DR-0880",
            latitude=18.5600,
            longitude=73.8100,
            # Carrying Deepak's load, so it is not a rescue candidate.
            status=TruckStatus.in_transit,
            refrigerated=False,
            max_volume_m3=18.0,
            max_weight_kg=5000.0,
        )
        trk_north_9110 = Truck(
            id="trk_north_9110",
            company_id=northline.id,
            registration_number="MH-14-EQ-9110",
            latitude=18.7300,
            longitude=73.6800,
            status=TruckStatus.idle,
            refrigerated=True,
            min_temp_c=-20.0,
            max_volume_m3=20.0,
            max_weight_kg=6000.0,
        )

        # Metro & Eagle fleet
        trk_metro_3320 = Truck(
            id="trk_metro_3320",
            company_id=metro.id,
            registration_number="MH-43-MS-3320",
            # ~7 km north of the Panvel stretch, far enough to be a genuine
            # candidate rather than a truck parked on top of the emergency.
            latitude=19.0910,
            longitude=73.0080,
            status=TruckStatus.idle,
            # Refrigerated, because it is seeded with a live offer on a
            # refrigerated load. An ambient truck bidding for insulin is an
            # offer the matcher's own compatibility check would have refused,
            # and showing one on the console contradicts the rule the demo is
            # there to demonstrate. It chills less deeply than Northline's,
            # which is the honest reason it scores lower.
            refrigerated=True,
            min_temp_c=-12.0,
            max_volume_m3=14.0,
            max_weight_kg=3500.0,
        )
        trk_eagle_8800 = Truck(
            id="trk_eagle_8800",
            company_id=eagle.id,
            registration_number="MH-04-EH-8800",
            latitude=18.9900,
            longitude=73.1100,
            status=TruckStatus.in_transit,
            refrigerated=False,
            hazmat_certified=True,
            max_volume_m3=28.0,
            max_weight_kg=12000.0,
        )
        session.add_all([
            trk_apex_9042, trk_apex_5510, trk_apex_2201, trk_apex_7740,
            trk_north_412, trk_north_880, trk_north_9110,
            trk_metro_3320, trk_eagle_8800,
        ])
        await session.flush()

        # ------------------------------------------------------------------
        # 3. Drivers
        # ------------------------------------------------------------------
        drv_apex_rajesh = Driver(
            id="drv_apex_rajesh",
            company_id=apex.id,
            name="Rajesh Kumar",
            email="rajesh@apexcold.example",
            hashed_password=hash_password(driver_pw),
            phone="+919840192831",
            assigned_truck_id=trk_apex_9042.id,
        )
        drv_apex_vikram = Driver(
            id="drv_apex_vikram",
            company_id=apex.id,
            name="Vikram Singh",
            email="vikram@apexcold.example",
            hashed_password=hash_password(driver_pw),
            phone="+919876543210",
            assigned_truck_id=trk_apex_5510.id,
        )
        drv_apex_amit = Driver(
            id="drv_apex_amit",
            company_id=apex.id,
            name="Amit Patil",
            email="amit@apexcold.example",
            hashed_password=hash_password(driver_pw),
            phone="+919822334455",
            assigned_truck_id=trk_apex_2201.id,
        )
        drv_north_sunil = Driver(
            id="drv_north_sunil",
            company_id=northline.id,
            name="Sunil Menon",
            email="sunil@northline.example",
            hashed_password=hash_password(driver_pw),
            phone="+919812233445",
            assigned_truck_id=trk_north_412.id,
        )
        drv_north_deepak = Driver(
            id="drv_north_deepak",
            company_id=northline.id,
            name="Deepak Shinde",
            email="deepak@northline.example",
            hashed_password=hash_password(driver_pw),
            phone="+919833445566",
            # Moved off the reefer so MH-14-EQ-9110 stays idle and available
            # to the split planner, and so Deepak has a load of his own.
            assigned_truck_id=trk_north_880.id,
        )
        session.add_all([
            drv_apex_rajesh, drv_apex_vikram, drv_apex_amit,
            drv_north_sunil, drv_north_deepak,
        ])
        await session.flush()

        # ------------------------------------------------------------------
        # 4. Shipments
        # ------------------------------------------------------------------
        shp_insulin = Shipment(
            id="shp_insulin_8492",
            owner_company_id=apex.id,
            truck_id=trk_apex_9042.id,
            cargo_type="Refrigerated insulin vials & vaccines",
            requires_refrigeration=True,
            required_max_temp_c=8.0,
            volume_m3=3.5,
            weight_kg=850.0,
            value_inr=480000.0,
            status="in_transit",
            origin_lat=18.5204,
            origin_lng=73.8567,
            destination_lat=19.0760,
            destination_lng=72.8777,
            destination_name="Bandra Central Hospital, Mumbai",
            planned_arrival_at=now + timedelta(hours=4),
        )
        # A load no single idle reefer nearby can take.
        #
        # The nearest compatible trucks are 24, 20 and 15 m3 against 38 m3 of
        # cargo, so this is the case the splitter exists for: three vehicles
        # are parked within twenty kilometres of a consignment that would
        # otherwise be written off as unrescuable.
        shp_bulk_vaccine = Shipment(
            id="shp_bulk_vax_7701",
            owner_company_id=apex.id,
            truck_id=trk_apex_5510.id,
            cargo_type="Bulk paediatric vaccine consignment",
            requires_refrigeration=True,
            required_max_temp_c=8.0,
            volume_m3=38.0,
            weight_kg=9500.0,
            value_inr=6200000.0,
            status="in_transit",
            origin_lat=18.7500,
            origin_lng=73.4200,
            destination_lat=18.5204,
            destination_lng=73.8567,
            destination_name="Pune Regional Vaccine Store",
            planned_arrival_at=now + timedelta(hours=5),
        )
        shp_biologics = Shipment(
            id="shp_biologics_3310",
            owner_company_id=apex.id,
            truck_id=trk_apex_5510.id,
            cargo_type="Monoclonal antibody sera",
            requires_refrigeration=True,
            required_max_temp_c=4.0,
            volume_m3=2.2,
            weight_kg=420.0,
            value_inr=1250000.0,
            status="in_transit",
            origin_lat=18.5204,
            origin_lng=73.8567,
            destination_lat=19.0330,
            destination_lng=73.0297,
            destination_name="Navi Mumbai Biohub",
            planned_arrival_at=now + timedelta(hours=2),
        )
        shp_semicon = Shipment(
            id="shp_semicon_904",
            owner_company_id=apex.id,
            truck_id=trk_apex_7740.id,
            cargo_type="Precision optics & test electronics",
            requires_refrigeration=False,
            volume_m3=6.0,
            weight_kg=1200.0,
            value_inr=2400000.0,
            status="in_transit",
            destination_name="Pune Tech Logistics Hub",
            planned_arrival_at=now + timedelta(hours=5),
        )
        shp_plasma = Shipment(
            id="shp_cryo_plasma_102",
            owner_company_id=apex.id,
            truck_id=trk_apex_2201.id,
            cargo_type="Fresh frozen donor plasma",
            requires_refrigeration=True,
            required_max_temp_c=-18.0,
            volume_m3=4.0,
            weight_kg=680.0,
            value_inr=750000.0,
            status="in_transit",
            destination_name="Apollo Blood Bank Center",
            planned_arrival_at=now + timedelta(hours=6),
        )
        shp_api = Shipment(
            id="shp_api_compound_77",
            owner_company_id=apex.id,
            truck_id=trk_north_412.id,
            cargo_type="API Oncology compounds (Rescued)",
            requires_refrigeration=True,
            required_max_temp_c=15.0,
            volume_m3=5.0,
            weight_kg=950.0,
            value_inr=820000.0,
            status="delivered",
            destination_name="Cipla Distribution Depot",
        )
        # Deepak's load, so the demo has a second driver who can actually
        # report a breakdown.
        #
        # Every driver except Rajesh was running empty, which meant the
        # breakdown button on a second phone failed with "no active shipment
        # assigned to this truck". This is an ambient load on an ambient
        # truck, deliberately at the other end of the corridor and belonging
        # to the other carrier -- so the rescue runs in the opposite
        # direction, with Apex's idle trucks as the candidates.
        shp_northline_parts = Shipment(
            id="shp_north_parts_5501",
            owner_company_id=northline.id,
            truck_id=trk_north_880.id,
            cargo_type="Automotive assembly components",
            requires_refrigeration=False,
            volume_m3=12.0,
            weight_kg=3800.0,
            value_inr=1450000.0,
            status="in_transit",
            origin_lat=18.5600,
            origin_lng=73.8100,
            destination_lat=19.0760,
            destination_lng=72.8777,
            destination_name="Bhiwandi Distribution Park",
            planned_arrival_at=now + timedelta(hours=6),
        )

        session.add_all([shp_insulin, shp_bulk_vaccine, shp_biologics, shp_semicon, shp_plasma, shp_api, shp_northline_parts])
        await session.flush()

        # ------------------------------------------------------------------
        # 5. Incidents
        # ------------------------------------------------------------------
        # Active incident: stranded reefer on Mumbai-Pune expressway
        inc_active = Incident(
            id="inc_apex_refrig_fail",
            shipment_id=shp_insulin.id,
            # Offers have already gone out below, so the incident is past
            # MATCHING. Seeding it as MATCHING left the data self-
            # contradictory: confirming one of its own offers was an illegal
            # transition, and the console got a 500.
            state=IncidentState.RESCUE_OFFERED,
            lat=18.5204,
            lng=73.8567,
            minutes_until_spoilage=84.0,
        )
        # The oversized consignment, still looking for capacity. Left at
        # MATCHING so the console can demonstrate both routes out of it:
        # fanning offers to carriers, and dividing the load when no single
        # one of them can take it.
        inc_bulk = Incident(
            id="inc_apex_bulk_vax",
            shipment_id=shp_bulk_vaccine.id,
            state=IncidentState.MATCHING,
            lat=18.6600,
            lng=73.7500,
            minutes_until_spoilage=140.0,
        )
        # Historic completed rescue
        inc_past = Incident(
            id="inc_apex_historic_01",
            shipment_id=shp_api.id,
            state=IncidentState.ESCROW_RELEASED,
            lat=18.9800,
            lng=73.1200,
            assigned_truck_id=trk_north_412.id,
        )
        session.add_all([inc_active, inc_bulk, inc_past])
        await session.flush()

        # Incident audit events
        ev1 = IncidentEvent(
            incident_id=inc_active.id,
            type="BREAKDOWN_REPORTED",
            actor_id=drv_apex_rajesh.id,
            previous_state=IncidentState.NORMAL.value,
            new_state=IncidentState.BREAKDOWN_REPORTED.value,
            metadata_json={"reason": "Refrigeration compressor gasket burst, temperature climbing rapidly"},
            created_at=now - timedelta(minutes=38),
        )
        ev2 = IncidentEvent(
            incident_id=inc_active.id,
            type="TRIAGED",
            actor_id="system",
            previous_state=IncidentState.BREAKDOWN_REPORTED.value,
            new_state=IncidentState.TRIAGING.value,
            metadata_json={"spoilage_threshold_c": 8.0, "current_temp_c": 6.8, "urgency": "CRITICAL"},
            created_at=now - timedelta(minutes=34),
        )
        ev3 = IncidentEvent(
            incident_id=inc_active.id,
            type="MATCHING_INITIATED",
            actor_id="system",
            previous_state=IncidentState.TRIAGING.value,
            new_state=IncidentState.MATCHING.value,
            metadata_json={"search_radius_km": 50.0, "candidates_evaluated": 6},
            created_at=now - timedelta(minutes=30),
        )
        # Events for past incident
        ev_p1 = IncidentEvent(
            incident_id=inc_past.id,
            type="DELIVERED",
            actor_id=drv_north_sunil.id,
            previous_state=IncidentState.RESCUE_IN_TRANSIT.value,
            new_state=IncidentState.DELIVERED.value,
            metadata_json={"destination": "Cipla Distribution Depot"},
            created_at=now - timedelta(days=2, hours=4),
        )
        ev_p2 = IncidentEvent(
            incident_id=inc_past.id,
            type="VERIFIED",
            actor_id="system",
            previous_state=IncidentState.DELIVERED.value,
            new_state=IncidentState.VERIFICATION.value,
            metadata_json={"cold_chain_breaches": 0, "avg_temp_c": 11.4},
            created_at=now - timedelta(days=2, hours=3, minutes=45),
        )
        ev_p3 = IncidentEvent(
            incident_id=inc_past.id,
            type="SETTLED",
            actor_id="system",
            previous_state=IncidentState.VERIFICATION.value,
            new_state=IncidentState.ESCROW_RELEASED.value,
            metadata_json={"released_amount_inr": 19800.0, "platform_fee_inr": 990.0},
            created_at=now - timedelta(days=2, hours=3, minutes=30),
        )
        session.add_all([ev1, ev2, ev3, ev_p1, ev_p2, ev_p3])
        await session.flush()

        # ------------------------------------------------------------------
        # 6. Rescue Offers
        # ------------------------------------------------------------------
        # Priced by the real pricing engine rather than by hand, so the
        # breakdown the console shows is arithmetic that actually happened.
        # Seeding a total with a plausible-looking breakdown beside it is how
        # a demo ends up displaying numbers that do not add up when somebody
        # in the audience checks them.
        quote_north = calculate_price(
            distance_km=8.4,
            requires_refrigeration=True,
            weight_kg=shp_insulin.weight_kg,
            volume_m3=shp_insulin.volume_m3,
            minutes_until_spoilage=inc_active.minutes_until_spoilage,
            num_compatible_nearby=3,
            cargo_value_inr=shp_insulin.value_inr,
            carrier_trust_score=94.0,
        )
        quote_metro = calculate_price(
            distance_km=19.2,
            requires_refrigeration=True,
            weight_kg=shp_insulin.weight_kg,
            volume_m3=shp_insulin.volume_m3,
            minutes_until_spoilage=inc_active.minutes_until_spoilage,
            num_compatible_nearby=3,
            cargo_value_inr=shp_insulin.value_inr,
            carrier_trust_score=75.0,
        )

        off_north = RescueOffer(
            id="off_north_412_ins",
            incident_id=inc_active.id,
            shipment_id=shp_insulin.id,
            owner_company_id=apex.id,
            carrier_company_id=northline.id,
            carrier_truck_id=trk_north_412.id,
            offer_round=1,
            price_total_inr=quote_north.total_inr,
            carrier_payout_inr=quote_north.carrier_payout_inr,
            platform_fee_inr=quote_north.platform_fee_inr,
            price_breakdown_json=quote_north.to_dict(),
            eta_minutes=18.0,
            distance_km=8.4,
            rescue_score=92.0,
            score_reasons_json=[
                "Reefer holds down to -25C (cargo needs <=8C)",
                "Distance 8.4 km (18m ETA)",
                "Trust score 89 + 4.9 peer star rating",
            ],
            carrier_accepted_at=now - timedelta(minutes=15),
            carrier_accepted_by=drv_north_sunil.id,
            state=OfferState.CARRIER_ACCEPTED,
            expires_at=now + timedelta(hours=3),
        )
        off_metro = RescueOffer(
            id="off_metro_332_ins",
            incident_id=inc_active.id,
            shipment_id=shp_insulin.id,
            owner_company_id=apex.id,
            carrier_company_id=metro.id,
            carrier_truck_id=trk_metro_3320.id,
            offer_round=1,
            price_total_inr=quote_metro.total_inr,
            carrier_payout_inr=quote_metro.carrier_payout_inr,
            platform_fee_inr=quote_metro.platform_fee_inr,
            price_breakdown_json=quote_metro.to_dict(),
            eta_minutes=34.0,
            distance_km=19.2,
            rescue_score=81.0,
            score_reasons_json=[
                "Rapid dispatch available",
                "Distance 19.2 km (34m ETA)",
            ],
            state=OfferState.PENDING,
            expires_at=now + timedelta(hours=3),
        )
        session.add_all([off_north, off_metro])
        await session.flush()

        # ------------------------------------------------------------------
        # 7. Escrows & Double-Entry Ledger
        # ------------------------------------------------------------------
        # The live incident deliberately has no escrow yet.
        #
        # An escrow is opened by the handshake, not before it: the money is
        # held at the moment the owner confirms a carrier who has already
        # accepted. Northline has accepted; Apex has not confirmed yet. A
        # seeded hold here would assert a state the system itself would never
        # produce, and it would skip the part worth watching -- confirming the
        # offer is what opens the escrow and posts the balancing hold.
        #
        # Historic settled escrow, so the ledger has real history behind it
        escrow_past = Escrow(
            id="esc_apex_hist_rel",
            incident_id=inc_past.id,
            owner_company_id=apex.id,
            carrier_company_id=northline.id,
            amount_inr=19800.0,
            carrier_payout_inr=18810.0,
            state="RELEASED",
        )
        session.add(escrow_past)
        await session.flush()

        # Balancing double-entry ledger postings: debit == credit strictly enforced
        led_p1 = LedgerEntry(
            escrow_id=escrow_past.id,
            posting_ref="ref_past_settle_01",
            posting_type="SETTLEMENT",
            account="ESCROW_HOLDING",
            debit_inr=19800.0,
            credit_inr=0.0,
            memo="Release of held escrow after verified cold-chain",
        )
        led_p2 = LedgerEntry(
            escrow_id=escrow_past.id,
            posting_ref="ref_past_settle_01",
            posting_type="SETTLEMENT",
            account="CARRIER_PAYOUT",
            debit_inr=0.0,
            credit_inr=18810.0,
            memo="Payout to Northline Freight",
        )
        led_p3 = LedgerEntry(
            escrow_id=escrow_past.id,
            posting_ref="ref_past_settle_01",
            posting_type="SETTLEMENT",
            account="PLATFORM_FEE",
            debit_inr=0.0,
            credit_inr=990.0,
            memo="Platform fee 5%",
        )
        session.add_all([led_p1, led_p2, led_p3])
        await session.flush()

        # ------------------------------------------------------------------
        # 7b. Safe storage network (relay destinations)
        # ------------------------------------------------------------------
        # Shared infrastructure along the Mumbai-Pune corridor. Deliberately
        # a mix: two cold stores with different temperature floors, a bonded
        # warehouse that cannot chill at all, a hazmat depot, and one site
        # that closes overnight. A relay list where every option works is not
        # a decision, and the console has to be able to show a real one.
        facilities = [
            StorageFacility(
                id="stor_bhiwandi_cold",
                name="Bhiwandi Cold Chain Hub",
                latitude=19.2963,
                longitude=73.0631,
                address="Kalyan-Bhiwandi Road, Bhiwandi, Thane",
                contact_phone="+91-22-2597-4410",
                refrigerated=True,
                min_temp_c=-25.0,
                max_temp_c=8.0,
                hazmat_approved=False,
                capacity_m3=4200.0,
                available_m3=980.0,
                handling_fee_inr=2400.0,
                storage_fee_inr_per_m3_day=185.0,
                open_24h=True,
            ),
            StorageFacility(
                id="stor_chakan_pharma",
                name="Chakan Pharma Cold Store",
                operator_company_id=northline.id,
                latitude=18.7606,
                longitude=73.8636,
                address="MIDC Phase II, Chakan, Pune",
                contact_phone="+91-20-6710-3388",
                refrigerated=True,
                min_temp_c=-20.0,
                max_temp_c=8.0,
                hazmat_approved=False,
                capacity_m3=2600.0,
                available_m3=640.0,
                handling_fee_inr=2100.0,
                storage_fee_inr_per_m3_day=210.0,
                open_24h=True,
            ),
            StorageFacility(
                id="stor_panvel_bonded",
                name="Panvel Bonded Warehouse",
                latitude=18.9894,
                longitude=73.1175,
                address="JNPT Feeder Road, Panvel, Raigad",
                contact_phone="+91-22-2745-9001",
                refrigerated=False,
                hazmat_approved=False,
                capacity_m3=8800.0,
                available_m3=3100.0,
                handling_fee_inr=1450.0,
                storage_fee_inr_per_m3_day=95.0,
                open_24h=True,
            ),
            StorageFacility(
                id="stor_talegaon_hazmat",
                name="Talegaon Hazardous Goods Depot",
                latitude=18.7351,
                longitude=73.6759,
                address="Talegaon MIDC, Pune",
                contact_phone="+91-20-6633-2200",
                refrigerated=False,
                hazmat_approved=True,
                capacity_m3=3400.0,
                available_m3=1250.0,
                handling_fee_inr=3800.0,
                storage_fee_inr_per_m3_day=240.0,
                open_24h=True,
            ),
            StorageFacility(
                id="stor_lonavala_transit",
                name="Lonavala Transit Cold Room",
                latitude=18.7546,
                longitude=73.4062,
                address="Old Mumbai-Pune Highway, Lonavala",
                contact_phone="+91-2114-27-3310",
                refrigerated=True,
                min_temp_c=2.0,
                max_temp_c=10.0,
                hazmat_approved=False,
                capacity_m3=900.0,
                available_m3=180.0,
                handling_fee_inr=1800.0,
                storage_fee_inr_per_m3_day=160.0,
                open_24h=False,
            ),
        ]
        session.add_all(facilities)
        await session.flush()

        # ------------------------------------------------------------------
        # 8. Telemetry Live States & Readings
        # ------------------------------------------------------------------
        live_states = [
            TruckLiveState(
                truck_id=trk_apex_9042.id,
                company_id=apex.id,
                latitude=18.5204,
                longitude=73.8567,
                speed_kph=0.0,
                heading_deg=115.0,
                battery_pct=88.0,
                is_moving=False,
                last_temperature_c=6.8,
                last_received_at=now,
                last_recorded_at=now,
                last_reading_at=now,
                dwell_started_at=now - timedelta(minutes=40),
                dwell_anchor_lat=18.5204,
                dwell_anchor_lng=73.8567,
            ),
            TruckLiveState(
                truck_id=trk_apex_5510.id,
                company_id=apex.id,
                latitude=18.7500,
                longitude=73.4200,
                speed_kph=62.0,
                heading_deg=310.0,
                battery_pct=94.0,
                is_moving=True,
                last_temperature_c=2.8,
                last_received_at=now,
                last_recorded_at=now,
                last_reading_at=now,
            ),
            TruckLiveState(
                truck_id=trk_apex_2201.id,
                company_id=apex.id,
                latitude=18.7200,
                longitude=73.8100,
                speed_kph=0.0,
                heading_deg=0.0,
                battery_pct=99.0,
                is_moving=False,
                last_temperature_c=-22.0,
                last_received_at=now,
                last_recorded_at=now,
                last_reading_at=now,
            ),
            TruckLiveState(
                truck_id=trk_apex_7740.id,
                company_id=apex.id,
                latitude=18.5100,
                longitude=73.9200,
                speed_kph=0.0,
                heading_deg=90.0,
                battery_pct=100.0,
                is_moving=False,
                last_received_at=now,
                last_recorded_at=now,
            ),
            TruckLiveState(
                truck_id=trk_north_412.id,
                company_id=northline.id,
                latitude=18.5850,
                longitude=73.7400,
                speed_kph=0.0,
                heading_deg=180.0,
                battery_pct=92.0,
                is_moving=False,
                last_temperature_c=-4.0,
                last_received_at=now,
                last_recorded_at=now,
                last_reading_at=now,
            ),
            TruckLiveState(
                truck_id=trk_north_880.id,
                company_id=northline.id,
                latitude=18.5600,
                longitude=73.8100,
                speed_kph=0.0,
                heading_deg=0.0,
                battery_pct=85.0,
                is_moving=False,
                last_received_at=now,
                last_recorded_at=now,
            ),
            TruckLiveState(
                truck_id=trk_north_9110.id,
                company_id=northline.id,
                latitude=18.7300,
                longitude=73.6800,
                speed_kph=0.0,
                heading_deg=45.0,
                battery_pct=90.0,
                is_moving=False,
                last_temperature_c=-8.0,
                last_received_at=now,
                last_recorded_at=now,
                last_reading_at=now,
            ),
            TruckLiveState(
                truck_id=trk_metro_3320.id,
                company_id=metro.id,
                latitude=19.0910,
                longitude=73.0080,
                speed_kph=0.0,
                heading_deg=270.0,
                battery_pct=76.0,
                is_moving=False,
                last_received_at=now,
                last_recorded_at=now,
            ),
            TruckLiveState(
                truck_id=trk_eagle_8800.id,
                company_id=eagle.id,
                latitude=18.9900,
                longitude=73.1100,
                speed_kph=48.0,
                heading_deg=340.0,
                battery_pct=82.0,
                is_moving=True,
                last_received_at=now,
                last_recorded_at=now,
            ),
        ]
        session.add_all(live_states)
        await session.flush()

        # ------------------------------------------------------------------
        # 8b. Where each truck has actually been
        # ------------------------------------------------------------------
        # Without this the console can draw a pin but not a trail, and the
        # fleet looks teleported rather than driven. Each moving truck gets a
        # track back along its heading over the last forty minutes, at the
        # cadence the driver app really uploads at.
        session.add_all(
            build_track_history(
                [
                    (trk_apex_9042.id, apex.id, drv_apex_rajesh.id, 18.5204, 73.8567, 0.0, 0.0),
                    (trk_apex_5510.id, apex.id, drv_apex_vikram.id, 18.7500, 73.4200, 62.0, 310.0),
                    (trk_apex_2201.id, apex.id, drv_apex_amit.id, 19.0330, 73.0297, 54.0, 295.0),
                    (trk_north_412.id, northline.id, drv_north_sunil.id, 18.5850, 73.7400, 48.0, 140.0),
                    (trk_north_880.id, northline.id, drv_north_deepak.id, 18.6100, 73.7900, 0.0, 0.0),
                ],
                now,
            )
        )
        await session.flush()
        await session.flush()

        # Cold-chain telemetry readings for the stranded insulin shipment:
        # Temperature climbing from 4.2C to 6.8C (approaching 8C threshold)
        temps = [4.2, 4.3, 4.5, 4.6, 4.8, 5.0, 5.3, 5.5, 5.8, 6.0, 6.2, 6.4, 6.6, 6.7, 6.8]
        cargo_readings = []
        for idx, temp in enumerate(temps):
            t_offset = (len(temps) - 1 - idx) * 5
            cargo_readings.append(
                CargoReading(
                    shipment_id=shp_insulin.id,
                    truck_id=trk_apex_9042.id,
                    company_id=apex.id,
                    incident_id=inc_active.id,
                    sensor_id="sens_apex_ble_09",
                    client_reading_id=f"read_ins_{idx:03d}",
                    recorded_at=now - timedelta(minutes=t_offset),
                    received_at=now - timedelta(minutes=t_offset),
                    temperature_c=temp,
                    humidity_pct=52.0 + idx * 0.4,
                    door_open=False,
                    reefer_state="FAULT",
                    reefer_setpoint_c=4.0,
                    sensor_battery_pct=95.0,
                    trust_level="VERIFIED_TEE",
                )
            )
        # Stable cold-chain readings for biologics shipment
        for idx, temp in enumerate([2.4, 2.5, 2.6, 2.5, 2.7, 2.6, 2.8, 2.8]):
            t_offset = (8 - idx) * 10
            cargo_readings.append(
                CargoReading(
                    shipment_id=shp_biologics.id,
                    truck_id=trk_apex_5510.id,
                    company_id=apex.id,
                    sensor_id="sens_apex_ble_44",
                    client_reading_id=f"read_bio_{idx:03d}",
                    recorded_at=now - timedelta(minutes=t_offset),
                    received_at=now - timedelta(minutes=t_offset),
                    temperature_c=temp,
                    humidity_pct=48.0,
                    door_open=False,
                    reefer_state="RUNNING",
                    reefer_setpoint_c=3.0,
                    sensor_battery_pct=99.0,
                    trust_level="VERIFIED_TEE",
                )
            )
        session.add_all(cargo_readings)
        await session.flush()

        # Telemetry Alerts
        alr1 = TelemetryAlert(
            id="alr_apex_temp_exc",
            company_id=apex.id,
            truck_id=trk_apex_9042.id,
            driver_id=drv_apex_rajesh.id,
            shipment_id=shp_insulin.id,
            incident_id=inc_active.id,
            alert_type=AlertType.TEMP_EXCURSION.value,
            severity=AlertSeverity.CRITICAL.value,
            status=AlertStatus.OPEN.value,
            dedupe_key="episode_temp_trk_apex_9042",
            opened_at=now - timedelta(minutes=35),
            last_seen_at=now,
            occurrence_count=5,
            peak_severity="CRITICAL",
            first_value_json={"temp_c": 6.1, "threshold_c": 8.0, "delta_c": 1.9},
            last_value_json={"temp_c": 6.8, "threshold_c": 8.0, "delta_c": 1.2, "status": "WARMING"},
        )
        alr2 = TelemetryAlert(
            id="alr_apex_dwell",
            company_id=apex.id,
            truck_id=trk_apex_9042.id,
            driver_id=drv_apex_rajesh.id,
            shipment_id=shp_insulin.id,
            incident_id=inc_active.id,
            alert_type=AlertType.DWELL.value,
            severity=AlertSeverity.WARN.value,
            status=AlertStatus.OPEN.value,
            dedupe_key="episode_dwell_trk_apex_9042",
            opened_at=now - timedelta(minutes=40),
            last_seen_at=now,
            occurrence_count=12,
            peak_severity="WARN",
            first_value_json={"dwell_minutes": 15},
            last_value_json={"dwell_minutes": 40, "highway_km": "NH48 Expressway KM 74"},
        )
        session.add_all([alr1, alr2])
        await session.flush()

        # ------------------------------------------------------------------
        # 9. SOS Emergencies & Cross-Carrier Broadcast
        # ------------------------------------------------------------------
        sos_alert = SosAlert(
            id="sos_eagle_blowout_01",
            client_request_id="req_eagle_sos_01",
            company_id=eagle.id,
            truck_id=trk_eagle_8800.id,
            category=SosCategory.MECHANICAL.value,
            severity=SosSeverity.HIGH.value,
            status=SosStatus.BROADCASTING.value,
            latitude=18.9900,
            longitude=73.1100,
            location_source="gps",
            landmark_note="Bhor Ghat expressway curve, double tyre blowout on heavy trailer",
            condition_note="Driver safe on shoulder, road partially obstructed",
            persons_affected=1,
            is_conscious=True,
            is_breathing=True,
            is_trapped=False,
            is_mobile=True,
            severe_bleeding=False,
            broadcast_radius_km=50.0,
            network_broadcast=True,
            reported_at=now - timedelta(minutes=25),
            broadcast_at=now - timedelta(minutes=25),
            created_by_type="company",
            created_by_id=eagle.id,
        )
        session.add(sos_alert)
        await session.flush()

        # Broadcast to nearby carriers on the network
        bcast1 = SosBroadcast(
            id="snd_bcast_apex",
            sos_id=sos_alert.id,
            recipient_company_id=apex.id,
            tier="NETWORK",
            channel="in_app",
            adapter_name="network_push",
            redaction_level="REDACTED",
            distance_km_at_send=14.2,
            adapter_status="DELIVERED",
            sent_at=now - timedelta(minutes=25),
            delivered_at=now - timedelta(minutes=25),
        )
        bcast2 = SosBroadcast(
            id="snd_bcast_north",
            sos_id=sos_alert.id,
            recipient_company_id=northline.id,
            tier="NETWORK",
            channel="in_app",
            adapter_name="network_push",
            redaction_level="REDACTED",
            distance_km_at_send=18.6,
            adapter_status="DELIVERED",
            sent_at=now - timedelta(minutes=25),
            delivered_at=now - timedelta(minutes=25),
        )
        session.add_all([bcast1, bcast2])
        await session.flush()

        # An emergency raised by the account the console signs in as, so the
        # SOS screen has its own live alert rather than only somebody else's.
        # A medical call is the case where the responder board matters most:
        # the dispatcher is deciding, minute by minute, whether the help
        # already coming is soon enough.
        apex_sos = SosAlert(
            id="sos_apex_medical_01",
            client_request_id="req_apex_sos_01",
            company_id=apex.id,
            driver_id=drv_apex_amit.id,
            truck_id=trk_apex_2201.id,
            shipment_id=shp_semicon.id,
            category=SosCategory.MEDICAL.value,
            severity=SosSeverity.CRITICAL.value,
            status=SosStatus.ACKNOWLEDGED.value,
            latitude=19.0330,
            longitude=73.0297,
            accuracy_m=9.0,
            location_source="gps",
            landmark_note="NH-48 km 42 southbound, past the Panvel toll",
            condition_note="Chest pain and sweating, pulled onto the shoulder",
            persons_affected=1,
            is_conscious=True,
            is_breathing=True,
            is_trapped=False,
            is_mobile=False,
            severe_bleeding=False,
            broadcast_radius_km=25.0,
            network_broadcast=True,
            ack_count=2,
            responder_count=1,
            reported_at=now - timedelta(minutes=6),
            broadcast_at=now - timedelta(minutes=6),
            first_ack_at=now - timedelta(minutes=4),
            created_by_type="driver",
            created_by_id=drv_apex_amit.id,
        )
        session.add(apex_sos)
        await session.flush()

        # Two carriers were told, at different distances.
        session.add_all([
            SosBroadcast(
                id="snd_apexsos_north",
                sos_id=apex_sos.id,
                recipient_company_id=northline.id,
                tier="NETWORK",
                channel="in_app",
                adapter_name="network_push",
                redaction_level="REDACTED",
                distance_km_at_send=6.4,
                adapter_status="DELIVERED",
                sent_at=now - timedelta(minutes=6),
                delivered_at=now - timedelta(minutes=6),
            ),
            SosBroadcast(
                id="snd_apexsos_metro",
                sos_id=apex_sos.id,
                recipient_company_id=metro.id,
                tier="NETWORK",
                channel="in_app",
                adapter_name="network_push",
                redaction_level="REDACTED",
                distance_km_at_send=11.8,
                adapter_status="DELIVERED",
                sent_at=now - timedelta(minutes=6),
                delivered_at=now - timedelta(minutes=6),
            ),
        ])

        # One is already moving with a stated ETA; the other has only
        # acknowledged. The board shows the difference, because "someone is
        # coming in 9 minutes" and "someone has seen it" are not the same
        # thing to a dispatcher deciding whether to escalate.
        session.add_all([
            SosResponse(
                id="rsp_apexsos_north",
                sos_id=apex_sos.id,
                responder_company_id=northline.id,
                responder_truck_id=trk_north_412.id,
                action="EN_ROUTE",
                eta_minutes=9.0,
                distance_km=6.4,
                note="Driver trained in first aid, diverting now",
                created_at=now - timedelta(minutes=3),
            ),
            SosResponse(
                id="rsp_apexsos_metro",
                sos_id=apex_sos.id,
                responder_company_id=metro.id,
                action="ACKNOWLEDGE",
                distance_km=11.8,
                created_at=now - timedelta(minutes=4),
            ),
        ])
        await session.flush()

        # ------------------------------------------------------------------
        # 10. Company Reputation & Ratings
        # ------------------------------------------------------------------
        north_rep = CompanyReputation(
            company_id=northline.id,
            offers_received=28,
            offers_accepted=24,
            offers_declined=3,
            offers_expired=1,
            avg_response_seconds=78.0,
            rescues_completed=22,
            rescues_cancelled_after_bind=0,
            disputes_raised_against=0,
            condition_breaches=0,
            rating_count=18,
            rating_sum=88,
            trust_score=94.0,
            signals_json=[
                {"kind": "positive", "label": "Cold-chain certified fleet (GDP/GWP compliant)"},
                {"kind": "positive", "label": "Sub-20min average rescue response"},
                {"kind": "positive", "label": "Zero temperature excursion disputes across 22 rescues"},
            ],
        )
        rating = RescueRating(
            id="rat_apex_north_past",
            offer_id=off_north.id,
            incident_id=inc_past.id,
            rater_role="owner",
            rater_company_id=apex.id,
            subject_company_id=northline.id,
            stars=5,
            tags_json=["on_time", "cold_chain_maintained", "smooth_cargo_transfer"],
            comment="Rapid arrival and pristine temperature maintenance for our oncology shipment.",
        )
        session.add_all([north_rep, rating])
        await session.commit()

        credentials = [
            ("Apex Cold Logistics (owner)", apex.email, apex_pw),
            ("Northline Freight (rescuer)", northline.email, north_pw),
            ("Metro Swift Logistics", metro.email, metro_pw),
            ("Eagle Heavy Haulage", eagle.email, eagle_pw),
            ("Rajesh Kumar (Apex driver)", drv_apex_rajesh.email, driver_pw),
            ("Vikram Singh (Apex driver)", drv_apex_vikram.email, driver_pw),
            ("Amit Patil (Apex driver)", drv_apex_amit.email, driver_pw),
            ("Sunil Menon (Northline driver)", drv_north_sunil.email, driver_pw),
            ("Deepak Shinde (Northline driver)", drv_north_deepak.email, driver_pw),
        ]
    return credentials


#: Where the seed leaves the accounts it just created.
#:
#: The desktop console reads this to offer one-click sign-in for the demo.
#: It holds live passwords, so it is gitignored and never shipped -- which is
#: also why the console degrades to an empty login form when it is absent
#: rather than falling back to a password baked into the source.
CREDENTIALS_FILE = ROOT / "demo_credentials.json"


def write_credentials_file(credentials: list[tuple[str, str, str]]) -> None:
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "accounts": [
            {"label": label, "email": email, "password": password}
            for label, email, password in credentials
        ],
    }
    CREDENTIALS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print()
    print(f"Credentials written to {CREDENTIALS_FILE.name} (gitignored).")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed CargoResQ rich demo data")
    parser.add_argument("--password", help="Use this password for every demo account")
    parser.add_argument("--reset", action="store_true", help="Delete demo rows first")
    args = parser.parse_args()

    credentials = asyncio.run(seed(args.password, args.reset))
    if not credentials:
        return 0

    write_credentials_file(credentials)

    print("\n==================================================================")
    print("CargoResQ Enterprise Demo Accounts Initialized")
    print("==================================================================\n")
    width = max(len(label) for label, _, _ in credentials)
    for label, email, pw in credentials:
        print(f"  {label.ljust(width)}  {email.ljust(28)}  {pw}")
    print("\nData summary:")
    print("  • 4 Companies (Apex Cold, Northline Freight, Metro Swift, Eagle Heavy)")
    print("  • 9 Trucks with live telemetry states across Mumbai-Pune expressway")
    print("  • 5 Drivers with mobile credentials")
    print("  • 5 Shipments (Active cold-chain, precision electronics, cryo plasma, oncology API)")
    print("  • 1 Urgent active breakdown incident with countdown & rescue offers")
    print("  • 1 Historic completed rescue with full audit trail")
    print("  • 5 Safe-storage facilities for cargo relay (cold, bonded, hazmat)")
    print("  • 1 Settled escrow with balanced double-entry ledger postings")
    print("    (the live rescue opens its own escrow when you confirm the offer)")
    print("  • 23 Cold-chain temperature telemetry logs & critical excursion alerts")
    print("  • 2 SOS emergencies: one raised by your own driver (with responders en route) and one cross-carrier broadcast\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
