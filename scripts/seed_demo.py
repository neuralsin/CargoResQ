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
from datetime import datetime, timedelta, timezone
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from services.core_api.app.auth import hash_password
from services.core_api.app.models import Company, Driver, Shipment, Truck, TruckStatus
from services.escrow_ledger.app.models import Escrow, LedgerEntry
from services.orchestrator.app.models import Incident, IncidentEvent, IncidentState
from services.orchestrator.app.offer_models import OfferState, RescueOffer
from services.orchestrator.app.reputation_models import CompanyReputation, RescueRating
from services.sos.app.models import (
    SosAlert,
    SosBroadcast,
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


async def seed(password: str | None, reset: bool) -> list[tuple[str, str, str]]:
    await ensure_schema()
    credentials: list[tuple[str, str, str]] = []

    async with async_session() as session:
        if reset:
            # Delete dependent entities in clean dependency order
            await session.execute(delete(RescueRating))
            await session.execute(delete(CompanyReputation))
            await session.execute(delete(SosBroadcast))
            await session.execute(delete(SosAlert))
            await session.execute(delete(TelemetryAlert))
            await session.execute(delete(CargoReading))
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
            status=TruckStatus.idle,
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
            latitude=19.0330,
            longitude=73.0297,
            status=TruckStatus.idle,
            refrigerated=False,
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
            assigned_truck_id=trk_north_9110.id,
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
        session.add_all([shp_insulin, shp_biologics, shp_semicon, shp_plasma, shp_api])
        await session.flush()

        # ------------------------------------------------------------------
        # 5. Incidents
        # ------------------------------------------------------------------
        # Active incident: stranded reefer on Mumbai-Pune expressway
        inc_active = Incident(
            id="inc_apex_refrig_fail",
            shipment_id=shp_insulin.id,
            state=IncidentState.MATCHING,
            lat=18.5204,
            lng=73.8567,
            minutes_until_spoilage=84.0,
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
        session.add_all([inc_active, inc_past])
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
        off_north = RescueOffer(
            id="off_north_412_ins",
            incident_id=inc_active.id,
            shipment_id=shp_insulin.id,
            owner_company_id=apex.id,
            carrier_company_id=northline.id,
            carrier_truck_id=trk_north_412.id,
            offer_round=1,
            price_total_inr=24500.0,
            carrier_payout_inr=23275.0,
            platform_fee_inr=1225.0,
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
            price_total_inr=28000.0,
            carrier_payout_inr=26600.0,
            platform_fee_inr=1400.0,
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
        # Active escrow holding for Northline's accepted offer
        escrow_active = Escrow(
            id="esc_apex_refrig_hold",
            incident_id=inc_active.id,
            owner_company_id=apex.id,
            carrier_company_id=northline.id,
            amount_inr=24500.0,
            carrier_payout_inr=23275.0,
            state="ACCEPTED",
        )
        # Historic settled escrow
        escrow_past = Escrow(
            id="esc_apex_hist_rel",
            incident_id=inc_past.id,
            owner_company_id=apex.id,
            carrier_company_id=northline.id,
            amount_inr=19800.0,
            carrier_payout_inr=18810.0,
            state="RELEASED",
        )
        session.add_all([escrow_active, escrow_past])
        await session.flush()

        # Balancing double-entry ledger postings: debit == credit strictly enforced
        led1 = LedgerEntry(
            escrow_id=escrow_active.id,
            posting_ref="ref_apex_hold_01",
            posting_type="HOLD",
            account="ESCROW_HOLDING",
            debit_inr=24500.0,
            credit_inr=0.0,
            memo="Escrow hold pending cargo rescue delivery",
        )
        led2 = LedgerEntry(
            escrow_id=escrow_active.id,
            posting_ref="ref_apex_hold_01",
            posting_type="HOLD",
            account="PAYER_DEPOSIT",
            debit_inr=0.0,
            credit_inr=24500.0,
            memo="Apex deposit for rescue dispatch",
        )
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
        session.add_all([led1, led2, led_p1, led_p2, led_p3])
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
                latitude=19.0330,
                longitude=73.0297,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed CargoResQ rich demo data")
    parser.add_argument("--password", help="Use this password for every demo account")
    parser.add_argument("--reset", action="store_true", help="Delete demo rows first")
    args = parser.parse_args()

    credentials = asyncio.run(seed(args.password, args.reset))
    if not credentials:
        return 0

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
    print("  • 2 Escrows with balanced double-entry ledger postings")
    print("  • 23 Cold-chain temperature telemetry logs & critical excursion alerts")
    print("  • 1 Cross-carrier SOS highway emergency broadcast\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
