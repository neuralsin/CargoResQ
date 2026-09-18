import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.mark.asyncio
async def test_driver_registration_and_workflow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        uid = uuid.uuid4().hex[:6]
        # 1. Register Company
        comp_res = await ac.post(
            "/api/v1/auth/register",
            json={
                "name": f"Apex Pharma {uid}",
                "email": f"ops_{uid}@apexpharma.com",
                "password": "Password123!",
            },
        )
        assert comp_res.status_code == 201
        comp_id = comp_res.json()["company_id"]
        comp_token = comp_res.json()["access_token"]
        comp_headers = {"Authorization": f"Bearer {comp_token}"}

        # 2. Add Truck
        truck_res = await ac.post(
            "/api/v1/trucks",
            headers=comp_headers,
            json={
                "registration_number": f"MH-12-{uid[:4].upper()}",
                "latitude": 18.5204,
                "longitude": 73.8567,
                "refrigerated": True,
                "min_temp_c": -20.0,
                "max_volume_m3": 12.0,
                "max_weight_kg": 3500.0,
            },
        )
        assert truck_res.status_code == 201
        truck_id = truck_res.json()["id"]

        # 3. Add Shipment
        ship_res = await ac.post(
            "/api/v1/shipments",
            headers=comp_headers,
            json={
                "truck_id": truck_id,
                "cargo_type": "Insulin Vials",
                "requires_refrigeration": True,
                "required_max_temp_c": 8.0,
                "volume_m3": 3.5,
                "weight_kg": 850.0,
                "value_inr": 480000.0,
            },
        )
        assert ship_res.status_code == 201
        shipment_id = ship_res.json()["id"]

        # 4. Register Driver
        driver_email = f"driver_{uid}@apexpharma.com"
        driver_pass = "DriverPass123!"
        drv_reg = await ac.post(
            "/api/v1/driver/register",
            headers=comp_headers,
            json={
                "name": f"Rajesh Kumar {uid}",
                "email": driver_email,
                "password": driver_pass,
                "phone": "+919876543210",
                "assigned_truck_id": truck_id,
            },
        )
        assert drv_reg.status_code == 201
        driver_token = drv_reg.json()["access_token"]
        driver_headers = {"Authorization": f"Bearer {driver_token}"}

        # 5. Driver Login
        login_res = await ac.post(
            "/api/v1/driver/login",
            data={"username": driver_email, "password": driver_pass},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert login_res.status_code == 200
        assert login_res.json()["access_token"]

        # 6. Driver Profile (GET /api/v1/driver/me)
        me_res = await ac.get("/api/v1/driver/me", headers=driver_headers)
        assert me_res.status_code == 200
        assert me_res.json()["email"] == driver_email
        assert me_res.json()["truck"]["id"] == truck_id

        # 7. Driver Active Shipment
        shipment_active_res = await ac.get(
            "/api/v1/driver/active-shipment", headers=driver_headers
        )
        assert shipment_active_res.status_code == 200
        assert shipment_active_res.json()["shipment"]["id"] == shipment_id

        # 8. Driver Report Breakdown SOS
        sos_res = await ac.post(
            "/api/v1/driver/report-breakdown",
            headers=driver_headers,
            json={
                "lat": 18.5204,
                "lng": 73.8567,
                "hours_to_spoilage": 0.75,
            },
        )
        assert sos_res.status_code == 200
        assert sos_res.json()["status"] == "breakdown_registered"

        # 9. Driver Active Incident
        inc_res = await ac.get(
            "/api/v1/driver/active-incident", headers=driver_headers
        )
        assert inc_res.status_code == 200
        assert inc_res.json()["incident"]["shipmentId"] == shipment_id
