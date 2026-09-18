"""
Porter-style fleet catalogue and fare estimation.

The tests that asserted on static/index.html and static/mobile.html are gone
along with those pages: they were Tailwind mockups whose data was a hardcoded
JavaScript array, and the API served them as if they were the product.
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_porter_fleet_vehicles(client: AsyncClient):
    if True:
        ac = client
        response = await ac.get("/api/v1/companies/vehicles")
        assert response.status_code == 200
        vehicles = response.json()
        assert len(vehicles) >= 5
        ids = [v["id"] for v in vehicles]
        assert "tata_ultra_reefer" in ids
        assert "tata_ace" in ids


@pytest.mark.asyncio
async def test_porter_fare_estimation(client: AsyncClient):
    if True:
        ac = client
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
async def test_company_fulfilment_rating(client: AsyncClient):
    if True:
        ac = client
        response = await ac.get("/api/v1/companies/1/fulfilment-rating")
        assert response.status_code == 200
        data = response.json()
        assert data["companyId"] == "1"
        assert data["overallRating"] >= 4.0
        assert data["coldChainComplianceRate"] > 95.0

