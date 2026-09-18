import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.mark.asyncio
async def test_desktop_console_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/")
        assert response.status_code == 200
        assert "CargoResQ" in response.text
        assert "Trip History" in response.text
        assert "Dijkstra" in response.text
        assert "fe7ec76159344885b9d0ebb1aafbf031" not in response.text  # Clean HTML


@pytest.mark.asyncio
async def test_mobile_driver_app_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/mobile")
        assert response.status_code == 200
        assert "CargoResQ" in response.text
        assert "1-Touch breakdown SOS" in response.text
        assert "SHP-8492" in response.text


@pytest.mark.asyncio
async def test_porter_fleet_vehicles():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/companies/vehicles")
        assert response.status_code == 200
        vehicles = response.json()
        assert len(vehicles) >= 5
        ids = [v["id"] for v in vehicles]
        assert "tata_ultra_reefer" in ids
        assert "tata_ace" in ids


@pytest.mark.asyncio
async def test_porter_fare_estimation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/v1/companies/estimate-fare",
            json={
                "vehicle_type": "tata_ultra_reefer",
                "distance_km": 25.0,
                "requires_loading_help": True,
                "is_urgent_rescue": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["vehicleType"] == "tata_ultra_reefer"
        assert data["estimatedTotalInr"] > 1800
        assert data["telemetryFee"] > 0


@pytest.mark.asyncio
async def test_company_fulfilment_rating():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/companies/1/fulfilment-rating")
        assert response.status_code == 200
        data = response.json()
        assert data["companyId"] == "1"
        assert data["overallRating"] >= 4.0
        assert data["coldChainComplianceRate"] > 95.0

