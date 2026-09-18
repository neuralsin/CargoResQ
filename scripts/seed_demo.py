"""
Seed a demonstration dataset.

Run explicitly -- never on boot. The previous implementation created accounts
with hardcoded passwords every time the application started, including in
production, and swallowed every failure into a debug log.

    python scripts/seed_demo.py                 # generated passwords, printed once
    python scripts/seed_demo.py --password X    # fixed password, for a scripted demo
    python scripts/seed_demo.py --reset         # delete existing demo rows first

Two carriers are created so the cross-carrier rescue path -- the entire point
of the product -- can actually be demonstrated: one strands a shipment, the
other has the compatible idle truck.
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from services.core_api.app.auth import hash_password
from services.core_api.app.models import Company, Driver, Shipment, Truck, TruckStatus
from shared.database import async_session
from shared.schema import ensure_schema
import shared.models_registry  # noqa: F401


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "Demo-" + "".join(secrets.choice(alphabet) for _ in range(12))


DEMO_COMPANY_IDS = ("comp_apex_pharma", "comp_northline")


async def seed(password: str | None, reset: bool) -> list[tuple[str, str, str]]:
    await ensure_schema()
    credentials: list[tuple[str, str, str]] = []

    async with async_session() as session:
        if reset:
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
        driver_pw = password or generate_password()

        apex = Company(
            id="comp_apex_pharma",
            name="Apex Cold Logistics",
            email="ops@apexcold.example",
            hashed_password=hash_password(apex_pw),
            trust_score=92.0,
        )
        northline = Company(
            id="comp_northline",
            name="Northline Freight",
            email="ops@northline.example",
            hashed_password=hash_password(north_pw),
            trust_score=88.0,
        )
        session.add_all([apex, northline])
        await session.flush()

        # Apex: the stranded reefer carrying temperature-sensitive cargo.
        apex_truck = Truck(
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
        # Northline: the competitor's compatible idle truck, ~9 km away.
        northline_truck = Truck(
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
        # A second Northline truck that is deliberately incompatible, so the
        # matching filter has something real to reject.
        northline_dry = Truck(
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
        session.add_all([apex_truck, northline_truck, northline_dry])
        await session.flush()

        shipment = Shipment(
            id="shp_insulin_8492",
            owner_company_id=apex.id,
            truck_id=apex_truck.id,
            cargo_type="Refrigerated insulin vials",
            requires_refrigeration=True,
            required_max_temp_c=8.0,
            volume_m3=3.5,
            weight_kg=850.0,
            value_inr=480000.0,
            status="in_transit",
        )
        session.add(shipment)

        driver = Driver(
            id="drv_apex_rajesh",
            company_id=apex.id,
            name="Rajesh Kumar",
            email="rajesh@apexcold.example",
            hashed_password=hash_password(driver_pw),
            phone="+919840192831",
            assigned_truck_id=apex_truck.id,
        )
        rescuer_driver = Driver(
            id="drv_north_sunil",
            company_id=northline.id,
            name="Sunil Menon",
            email="sunil@northline.example",
            hashed_password=hash_password(driver_pw),
            phone="+919812233445",
            assigned_truck_id=northline_truck.id,
        )
        session.add_all([driver, rescuer_driver])
        await session.commit()

        credentials = [
            ("Apex Cold Logistics (owner)", apex.email, apex_pw),
            ("Northline Freight (rescuer)", northline.email, north_pw),
            ("Rajesh Kumar (Apex driver)", driver.email, driver_pw),
            ("Sunil Menon (Northline driver)", rescuer_driver.email, driver_pw),
        ]
    return credentials


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed CargoResQ demo data")
    parser.add_argument("--password", help="Use this password for every demo account")
    parser.add_argument("--reset", action="store_true", help="Delete demo rows first")
    args = parser.parse_args()

    credentials = asyncio.run(seed(args.password, args.reset))
    if not credentials:
        return 0

    print("\nDemo accounts created. These passwords are shown once:\n")
    width = max(len(label) for label, _, _ in credentials)
    for label, email, pw in credentials:
        print(f"  {label.ljust(width)}  {email}  {pw}")
    print("\nThese are demonstration accounts. Do not seed them into a production database.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
