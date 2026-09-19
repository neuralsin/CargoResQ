"""
HTTP client for the CargoResQ operations console.

One place that knows how to talk to the API, so the UI never builds a URL or
a header itself. Errors surface as ApiError with the server's own message --
there is no fallback that invents plausible data when a call fails, because a
console that looks fine while the backend is down is worse than one that says
so.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

#: Historically misspelled (CARGOESQ, no R). The correct spelling wins; the
#: old one is still honoured so existing setups keep working.
DEFAULT_API_URL = (
    os.getenv("CARGORESQ_API_URL")
    or os.getenv("CARGOESQ_API_URL")
    or "http://localhost:8000"
)

REQUEST_TIMEOUT = 15.0


class ApiError(RuntimeError):
    """A call failed. Carries the server's message where there is one."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class CargoResQClient:
    def __init__(self, base_url: str = DEFAULT_API_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self.company_id: Optional[str] = None
        self.company_name: Optional[str] = None
        self.role: Optional[str] = None

    # -- plumbing --------------------------------------------------------
    @property
    def websocket_url(self) -> str:
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        host = self.base_url.split("://", 1)[-1]
        return f"{scheme}://{host}/ws"

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        try:
            response = httpx.request(
                method, url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs
            )
        except httpx.HTTPError as exc:
            raise ApiError(f"Could not reach CargoResQ API at {self.base_url}: {exc}")

        if response.status_code >= 400:
            detail: Any
            try:
                body = response.json()
                detail = body.get("detail", body)
            except Exception:
                detail = response.text[:300]
            if isinstance(detail, dict):
                detail = detail.get("message") or detail
            raise ApiError(f"{response.status_code}: {detail}", response.status_code)

        if not response.content:
            return None
        return response.json()

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs)

    # -- auth ------------------------------------------------------------
    def login(self, email: str, password: str) -> Dict[str, Any]:
        data = self._request(
            "POST",
            "/api/v1/auth/login",
            data={"username": email, "password": password},
        )
        self.token = data["access_token"]
        self.company_id = data["company_id"]
        self.company_name = data["company_name"]
        self.role = data["role"]
        return data

    def logout(self) -> None:
        self.token = None
        self.company_id = None
        self.company_name = None
        self.role = None

    # -- core reads ------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        return self.get("/health")

    def company(self) -> Dict[str, Any]:
        return self.get("/api/v1/companies/me")

    def incidents(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.get(f"/api/v1/incidents?limit={limit}")

    def incident(self, incident_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/incidents/{incident_id}")

    def incident_timeline(self, incident_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/api/v1/incidents/{incident_id}/timeline")

    def incident_sla(self, incident_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/incidents/{incident_id}/sla")

    def trucks(self) -> List[Dict[str, Any]]:
        return self.get("/api/v1/trucks")

    def shipments(self) -> List[Dict[str, Any]]:
        return self.get("/api/v1/shipments")

    # -- live positions --------------------------------------------------
    def fleet_live(self) -> List[Dict[str, Any]]:
        """Own fleet with current positions, flagged live or stale."""
        return self.get("/api/v1/telemetry/fleet/live")

    def truck_live(self, truck_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/telemetry/trucks/{truck_id}/live")

    def truck_track(self, truck_id: str, hours: float = 6.0) -> List[Dict[str, Any]]:
        return self.get(f"/api/v1/telemetry/trucks/{truck_id}/track?hours={hours}")

    # -- rescue ----------------------------------------------------------
    def candidates(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/api/v1/matching/candidates", json=payload)

    def fan_out_offers(
        self, incident_id: str, radius_km: float = 50.0, max_candidates: int = 5
    ) -> Dict[str, Any]:
        return self.post(
            f"/api/v1/incidents/{incident_id}/offers",
            json={
                "radius_km": radius_km,
                "max_candidates": max_candidates,
                "ttl_seconds": 300,
            },
        )

    def incident_offers(self, incident_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/api/v1/incidents/{incident_id}/offers")

    def offer_inbox(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        suffix = f"?state={state}" if state else ""
        return self.get(f"/api/v1/offers/inbox{suffix}")

    def confirm_offer(self, offer_id: str) -> Dict[str, Any]:
        return self.post(f"/api/v1/offers/{offer_id}/owner-confirm")

    def withdraw_offer(self, offer_id: str, reason: str = "") -> Dict[str, Any]:
        return self.post(
            f"/api/v1/offers/{offer_id}/owner-withdraw", json={"reason": reason or None}
        )

    def accept_offer_as_carrier(self, offer_id: str) -> Dict[str, Any]:
        return self.post(f"/api/v1/offers/{offer_id}/carrier-accept")

    def decline_offer_as_carrier(self, offer_id: str, reason: str = "") -> Dict[str, Any]:
        return self.post(
            f"/api/v1/offers/{offer_id}/carrier-decline", json={"reason": reason or None}
        )

    def cancel_rescue(self, offer_id: str, reason: str) -> Dict[str, Any]:
        return self.post(f"/api/v1/offers/{offer_id}/cancel", json={"reason": reason})

    def advance_incident(
        self, incident_id: str, new_state: str, metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        return self.post(
            f"/api/v1/incidents/{incident_id}/advance",
            json={"new_state": new_state, "metadata": metadata},
        )

    def active_rescues(self) -> List[Dict[str, Any]]:
        """Rescues we are performing for another carrier, with live tracking."""
        return self.get("/api/v1/rescues/active")

    def stand_down_incident(self, incident_id: str, reason: str = "") -> Dict[str, Any]:
        """Take back a breakdown, unwinding offers, escrow and truck holds."""
        return self.post(
            f"/api/v1/incidents/{incident_id}/stand-down",
            json={"reason": reason or None},
        )

    # -- relay and split --------------------------------------------------
    def storage_facilities(
        self, lat: Optional[float] = None, lng: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        query = f"?lat={lat}&lng={lng}" if lat is not None and lng is not None else ""
        return self.get(f"/api/v1/storage/facilities{query}")

    def relay_options(self, incident_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/incidents/{incident_id}/relay-options")

    def relay_to_storage(
        self, incident_id: str, facility_id: str, reason: str = ""
    ) -> Dict[str, Any]:
        return self.post(
            f"/api/v1/incidents/{incident_id}/relay",
            json={"facility_id": facility_id, "reason": reason or None},
        )

    def split_plan(self, incident_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/incidents/{incident_id}/split-plan")

    def reputation(self, company_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/companies/{company_id}/reputation")

    # -- money -----------------------------------------------------------
    def escrow(self, escrow_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/escrow/{escrow_id}")

    def escrow_ledger(self, escrow_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/escrow/{escrow_id}/ledger")

    def escrow_transition(self, escrow_id: str, new_state: str) -> Dict[str, Any]:
        return self.post(
            f"/api/v1/escrow/{escrow_id}/transition", json={"new_state": new_state}
        )

    def escrow_verify(self, escrow_id: str) -> Dict[str, Any]:
        return self.post(f"/api/v1/escrow/{escrow_id}/verify")

    def pricing_policy(self) -> Dict[str, Any]:
        return self.get("/api/v1/pricing/policy")

    def quote(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/api/v1/pricing/quote", json=payload)

    # -- telemetry and safety --------------------------------------------
    def alerts(self, status: str = "OPEN", limit: int = 50) -> List[Dict[str, Any]]:
        return self.get(f"/api/v1/telemetry/alerts?status={status}&limit={limit}")

    def acknowledge_alert(self, alert_id: str) -> Dict[str, Any]:
        return self.post(f"/api/v1/telemetry/alerts/{alert_id}/acknowledge")

    def resolve_alert(self, alert_id: str, note: str = "") -> Dict[str, Any]:
        return self.post(
            f"/api/v1/telemetry/alerts/{alert_id}/resolve",
            json={"resolution_note": note or None},
        )

    def cold_chain(self, shipment_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/telemetry/shipments/{shipment_id}/cold-chain")

    def active_sos(self) -> List[Dict[str, Any]]:
        return self.get("/api/v1/sos/active")

    def nearby_sos(self) -> List[Dict[str, Any]]:
        """Cross-carrier alerts this company was told about."""
        return self.get("/api/v1/sos/nearby")

    def sos(self, sos_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/sos/{sos_id}")

    def acknowledge_sos(self, sos_id: str) -> Dict[str, Any]:
        return self.post(f"/api/v1/sos/{sos_id}/acknowledge")

    #: The only actions the server accepts. Sending anything else is a 422,
    #: and it used to be sent from a button labelled "Dispatch rescue".
    SOS_ACTIONS = ("EN_ROUTE", "ON_SCENE", "STOOD_DOWN", "UNABLE", "INFO")

    def respond_to_sos(
        self, sos_id: str, action: str, eta_minutes: Optional[float] = None
    ) -> Dict[str, Any]:
        if action not in self.SOS_ACTIONS:
            raise ApiError(
                f"{action!r} is not a valid SOS response. "
                f"Expected one of {list(self.SOS_ACTIONS)}."
            )
        return self.post(
            f"/api/v1/sos/{sos_id}/respond",
            json={"action": action, "eta_minutes": eta_minutes},
        )

    def escalate_sos_to_incident(self, sos_id: str) -> Dict[str, Any]:
        """Turn an SOS into a rescue incident that carriers can be offered."""
        return self.post(f"/api/v1/sos/{sos_id}/escalate-to-incident")

    def resolve_sos(self, sos_id: str, code: str, note: str = "") -> Dict[str, Any]:
        return self.post(
            f"/api/v1/sos/{sos_id}/status",
            json={
                "status": "RESOLVED",
                "resolution_code": code,
                "resolution_note": note or None,
            },
        )

    # -- fleet management -------------------------------------------------
    def create_truck(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/api/v1/trucks", json=payload)

    def create_shipment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/api/v1/shipments", json=payload)

    def report_breakdown(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post("/api/v1/breakdowns", json=payload)

    def scenarios(self) -> List[Dict[str, Any]]:
        return self.get("/api/v1/simulation/scenarios")

    def run_simulation(self, scenario: str) -> Dict[str, Any]:
        return self.post("/api/v1/simulation/run", json={"scenario": scenario})
