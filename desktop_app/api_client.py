"""Small, typed-enough HTTP client for the native operations console."""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx


DEFAULT_API_URL = os.getenv("CARGOESQ_API_URL", "http://localhost:8000")


class ApiError(RuntimeError):
    pass


class CargoResQClient:
    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = (base_url or DEFAULT_API_URL).rstrip("/")
        self.token: Optional[str] = None

    @property
    def websocket_url(self) -> str:
        scheme = "wss" if self.base_url.startswith("https://") else "ws"
        return scheme + "://" + self.base_url.split("://", 1)[-1] + "/ws"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", {})
        headers = {**self._headers(), **headers}
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=12.0,
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise ApiError(f"Could not reach CargoResQ API: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(f"API {response.status_code}: {detail}")
        if not response.content:
            return None
        return response.json()

    def login(self, email: str, password: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        self.token = data["access_token"]
        return data

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def company(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/companies/me")

    def incidents(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/incidents")

    def incident_timeline(self, incident_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/v1/incidents/{incident_id}/timeline")

    def trucks(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/trucks")

    def shipments(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/shipments")

    def vehicles(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/companies/vehicles")

    def estimate_fare(
        self,
        vehicle_type: str,
        distance_km: float,
        requires_loading_help: bool = False,
        is_urgent_rescue: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/companies/estimate-fare",
            json={
                "vehicle_type": vehicle_type,
                "distance_km": distance_km,
                "requires_loading_help": requires_loading_help,
                "is_urgent_rescue": is_urgent_rescue,
            },
        )

    def dijkstra_fastest_route(
        self, start_node: str, target_node: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/matching/dijkstra",
            json={
                "start_node": start_node,
                "target_node": target_node,
                "cargo_type": "refrigerated_pharma",
            },
        )

    def scenarios(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/simulation/scenarios")

    def run_simulation(self, scenario: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/simulation/run",
            json={"scenario": scenario, "seconds_per_simulated_minute": 0.05},
        )
