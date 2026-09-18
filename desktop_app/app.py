"""CargoResQ native operations console.

The desktop product is a real CustomTkinter application. It connects directly
to the configured API endpoint and can be packaged as a Windows executable
with PyInstaller.
"""
from __future__ import annotations

import threading
import tkinter.messagebox as messagebox
from datetime import datetime
from typing import Any, Callable

import customtkinter as ctk

try:
    from .api_client import ApiError, CargoResQClient
except ImportError:  # PyInstaller executes the entry script outside the package.
    from api_client import ApiError, CargoResQClient


COLORS = {
    "ink": "#101615",
    "muted": "#6F7B78",
    "line": "#E4EAE7",
    "canvas": "#F5F7F6",
    "card": "#FFFFFF",
    "teal": "#0F766E",
    "teal_soft": "#E5F3F0",
    "amber": "#B7791F",
    "amber_soft": "#FFF4D8",
    "red": "#B42318",
    "red_soft": "#FDECEA",
}


def state_label(state: str) -> str:
    return state.replace("_", " ").title()


def state_color(state: str) -> tuple[str, str]:
    if state in {"ESCROW_RELEASED", "DELIVERED"}:
        return COLORS["teal"], COLORS["teal_soft"]
    if state in {"CANCELLED", "DISPUTED"}:
        return COLORS["red"], COLORS["red_soft"]
    if state in {"BREAKDOWN_REPORTED", "TRIAGING", "MATCHING"}:
        return COLORS["amber"], COLORS["amber_soft"]
    return COLORS["teal"], COLORS["teal_soft"]


class LoginView(ctk.CTkFrame):
    def __init__(self, master: "CargoResQApp", on_login: Callable[[str, str, str], None]) -> None:
        super().__init__(master, fg_color=COLORS["canvas"])
        self.on_login = on_login
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        card = ctk.CTkFrame(self, fg_color=COLORS["card"], corner_radius=16, border_width=1, border_color=COLORS["line"])
        card.grid(row=0, column=0, padx=32, pady=32)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="CargoResQ", text_color=COLORS["ink"], font=ctk.CTkFont(size=30, weight="bold")).grid(row=0, column=0, padx=40, pady=(34, 2))
        ctk.CTkLabel(card, text="LIVE OPERATIONS CONSOLE", text_color=COLORS["teal"], font=ctk.CTkFont(size=11, weight="bold")).grid(row=1, column=0, padx=40, pady=(0, 24))

        ctk.CTkLabel(card, text="API Endpoint", anchor="w", text_color=COLORS["muted"]).grid(row=2, column=0, sticky="ew", padx=40, pady=(0, 4))
        self.endpoint = ctk.CTkEntry(card, width=380, height=38, border_color=COLORS["line"], fg_color="#FAFCFB")
        self.endpoint.insert(0, master.client.base_url)
        self.endpoint.grid(row=3, column=0, padx=40, pady=(0, 14))

        ctk.CTkLabel(card, text="Company Email", anchor="w", text_color=COLORS["muted"]).grid(row=4, column=0, sticky="ew", padx=40, pady=(0, 4))
        self.email = ctk.CTkEntry(card, width=380, height=38, border_color=COLORS["line"], fg_color="#FAFCFB")
        self.email.insert(0, "ops@cargoresq.com")
        self.email.grid(row=5, column=0, padx=40, pady=(0, 14))

        ctk.CTkLabel(card, text="Password", anchor="w", text_color=COLORS["muted"]).grid(row=6, column=0, sticky="ew", padx=40, pady=(0, 4))
        self.password = ctk.CTkEntry(card, width=380, height=38, show="•", border_color=COLORS["line"], fg_color="#FAFCFB")
        self.password.insert(0, "Password123!")
        self.password.grid(row=7, column=0, padx=40, pady=(0, 16))

        self.status = ctk.CTkLabel(card, text="", text_color=COLORS["red"], wraplength=360)
        self.status.grid(row=8, column=0, padx=40, pady=(0, 10))

        self.button = ctk.CTkButton(card, text="Sign in to Operations", height=42, fg_color=COLORS["teal"], hover_color="#0A5C56", command=self.submit)
        self.button.grid(row=9, column=0, padx=40, pady=(0, 10), sticky="ew")

        demo_btn = ctk.CTkButton(card, text="One-Click Demo Login (Apex Pharma)", height=36, fg_color="#E9F2EF", text_color=COLORS["teal"], hover_color="#D8E8E4", command=self.quick_demo_login)
        demo_btn.grid(row=10, column=0, padx=40, pady=(0, 32), sticky="ew")

        self.password.bind("<Return>", lambda _event: self.submit())

    def quick_demo_login(self) -> None:
        self.email.delete(0, "end")
        self.email.insert(0, "ops@cargoresq.com")
        self.password.delete(0, "end")
        self.password.insert(0, "Password123!")
        self.submit()

    def submit(self) -> None:
        self.button.configure(state="disabled", text="Connecting…")
        self.status.configure(text="")
        threading.Thread(target=self._submit_background, daemon=True).start()

    def _submit_background(self) -> None:
        try:
            self.on_login(self.endpoint.get().strip(), self.email.get().strip(), self.password.get())
        except ApiError as exc:
            self.after(0, lambda: self._failed(str(exc)))

    def _failed(self, message: str) -> None:
        self.status.configure(text=message)
        self.button.configure(state="normal", text="Sign in to Operations")


class StatCard(ctk.CTkFrame):
    def __init__(self, master: Any, title: str, value: str, detail: str, accent: str = COLORS["teal"]) -> None:
        super().__init__(master, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        ctk.CTkLabel(self, text=title.upper(), text_color=COLORS["muted"], font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(self, text=value, text_color=COLORS["ink"], font=ctk.CTkFont(size=26, weight="bold")).pack(anchor="w", padx=16)
        ctk.CTkLabel(self, text=detail, text_color=accent, font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=16, pady=(2, 14))


class DashboardView(ctk.CTkFrame):
    def __init__(self, master: "CargoResQApp") -> None:
        super().__init__(master, fg_color=COLORS["canvas"])
        self.master_app = master
        self.active_tab = "Command center"
        self.incidents: list[dict[str, Any]] = []
        self.trucks: list[dict[str, Any]] = []
        self.shipments_data: list[dict[str, Any]] = []
        self.selected_incident: dict[str, Any] | None = None
        self.sidebar_buttons: dict[str, ctk.CTkButton] = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_container()
        self.refresh()

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=COLORS["ink"])
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="CargoResQ", text_color="#FFFFFF", font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w", padx=22, pady=(28, 2))
        ctk.CTkLabel(sidebar, text="RESCUE CONTROL", text_color="#8CCBC3", font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=22, pady=(0, 30))

        tabs = ["Command center", "Fleet", "Shipments", "Audit trail", "Dijkstra Route", "Porter Estimator"]
        for label in tabs:
            is_active = label == self.active_tab
            btn = ctk.CTkButton(
                sidebar,
                text=label,
                anchor="w",
                height=38,
                fg_color="#21423F" if is_active else "transparent",
                hover_color="#21423F",
                text_color="#EAF5F2" if is_active else "#B5C8C4",
                command=lambda name=label: self.switch_tab(name),
            )
            btn.pack(fill="x", padx=12, pady=2)
            self.sidebar_buttons[label] = btn

        ctk.CTkLabel(sidebar, text="SIMULATION LAB", text_color="#7F9793", font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=22, pady=(26, 6))
        self.scenario = ctk.CTkOptionMenu(sidebar, values=["cold_chain_critical", "competing_rescuers", "hazmat_no_match"], height=34, fg_color="#21423F", button_color=COLORS["teal"], button_hover_color="#0A5C56")
        self.scenario.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkButton(sidebar, text="Run scenario", height=36, fg_color=COLORS["teal"], hover_color="#0A5C56", command=self.run_simulation).pack(fill="x", padx=12)

        ctk.CTkLabel(sidebar, text="", text_color="#B5C8C4").pack(expand=True)
        ctk.CTkButton(sidebar, text="Sign out", anchor="w", height=38, fg_color="transparent", hover_color="#21423F", text_color="#B5C8C4", command=self.master_app.show_login).pack(fill="x", padx=12, pady=(0, 20))

    def _build_main_container(self) -> None:
        self.main_area = ctk.CTkFrame(self, fg_color=COLORS["canvas"], corner_radius=0)
        self.main_area.grid(row=0, column=1, sticky="nsew", padx=26, pady=24)
        self.main_area.grid_columnconfigure(0, weight=1)
        self.main_area.grid_rowconfigure(2, weight=1)

        # Header Bar
        header = ctk.CTkFrame(self.main_area, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.grid_columnconfigure(0, weight=1)
        self.view_title = ctk.CTkLabel(header, text="Command center", text_color=COLORS["ink"], font=ctk.CTkFont(size=26, weight="bold"))
        self.view_title.grid(row=0, column=0, sticky="w")

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.connection = ctk.CTkLabel(right, text="● checking API", text_color=COLORS["muted"], font=ctk.CTkFont(size=11, weight="bold"))
        self.connection.pack(side="left", padx=(0, 14))
        ctk.CTkButton(right, text="Refresh", width=85, height=32, fg_color=COLORS["card"], hover_color="#E9EFEC", text_color=COLORS["ink"], border_width=1, border_color=COLORS["line"], command=self.refresh).pack(side="left")

        # Stats Cards Bar
        self.stats = ctk.CTkFrame(self.main_area, fg_color="transparent")
        self.stats.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        for i in range(4):
            self.stats.grid_columnconfigure(i, weight=1)

        # Dynamic View Content Container
        self.dynamic_content = ctk.CTkFrame(self.main_area, fg_color="transparent")
        self.dynamic_content.grid(row=2, column=0, sticky="nsew")
        self.dynamic_content.grid_columnconfigure(0, weight=1)
        self.dynamic_content.grid_rowconfigure(0, weight=1)

    def switch_tab(self, tab_name: str) -> None:
        self.active_tab = tab_name
        self.view_title.configure(text=tab_name)
        for name, btn in self.sidebar_buttons.items():
            is_active = name == tab_name
            btn.configure(
                fg_color="#21423F" if is_active else "transparent",
                text_color="#EAF5F2" if is_active else "#B5C8C4",
            )
        self.render_active_view()

    def render_active_view(self) -> None:
        for child in self.dynamic_content.winfo_children():
            child.destroy()

        if self.active_tab == "Command center":
            self._render_command_center()
        elif self.active_tab == "Fleet":
            self._render_fleet_view()
        elif self.active_tab == "Shipments":
            self._render_shipments_view()
        elif self.active_tab == "Audit trail":
            self._render_audit_trail_view()
        elif self.active_tab == "Dijkstra Route":
            self._render_dijkstra_view()
        elif self.active_tab == "Porter Estimator":
            self._render_porter_view()

    def _render_command_center(self) -> None:
        content = ctk.CTkFrame(self.dynamic_content, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=3)
        content.grid_columnconfigure(1, weight=2)
        content.grid_rowconfigure(0, weight=1)

        # Left: Active Incidents
        card_inc = ctk.CTkFrame(content, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        card_inc.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        card_inc.grid_rowconfigure(1, weight=1)
        card_inc.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card_inc, text="Active Incidents", text_color=COLORS["ink"], font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(card_inc, text="Every driver SOS triggers an atomic rescue lifecycle.", text_color=COLORS["muted"], font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w", padx=18, pady=(40, 0))
        self.incident_list = ctk.CTkScrollableFrame(card_inc, fg_color="transparent")
        self.incident_list.grid(row=1, column=0, sticky="nsew", padx=10, pady=12)

        if not self.incidents:
            ctk.CTkLabel(self.incident_list, text="No active incidents for this carrier.", text_color=COLORS["muted"]).pack(pady=28)
        else:
            for incident in self.incidents:
                self._add_incident_row(incident)

        # Right: Live Rescue Vector Map
        card_map = ctk.CTkFrame(content, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        card_map.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        card_map.grid_rowconfigure(1, weight=1)
        card_map.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(card_map, text="Live Rescue Map", text_color=COLORS["ink"], font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        ctk.CTkLabel(card_map, text="Carrier trucks and emergency distress waypoints", text_color=COLORS["muted"], font=ctk.CTkFont(size=11)).grid(row=0, column=0, sticky="w", padx=18, pady=(40, 0))
        self.map_canvas = ctk.CTkCanvas(card_map, bg="#E9F0ED", highlightthickness=0)
        self.map_canvas.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        self.map_canvas.bind("<Configure>", lambda _event: self.draw_map())
        self.draw_map()

    def _render_fleet_view(self) -> None:
        card = ctk.CTkFrame(self.dynamic_content, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        card.pack(fill="both", expand=True)
        ctk.CTkLabel(card, text="Registered Fleet Vehicles", text_color=COLORS["ink"], font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(card, text="Carrier-owned refrigerated and ambient trucks registered in CargoResQ.", text_color=COLORS["muted"], font=ctk.CTkFont(size=12)).pack(anchor="w", padx=22, pady=(0, 14))

        list_frame = ctk.CTkScrollableFrame(card, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=16, pady=8)

        if not self.trucks:
            ctk.CTkLabel(list_frame, text="No trucks found for this carrier.", text_color=COLORS["muted"]).pack(pady=40)
        else:
            for truck in self.trucks:
                row = ctk.CTkFrame(list_frame, fg_color="#FAFCFB", corner_radius=10, border_width=1, border_color=COLORS["line"])
                row.pack(fill="x", pady=5, padx=4)
                left = ctk.CTkFrame(row, fg_color="transparent")
                left.pack(side="left", padx=16, pady=12)
                ctk.CTkLabel(left, text=truck.get("registration_number", "TRK-000"), font=ctk.CTkFont(size=14, weight="bold"), text_color=COLORS["ink"]).pack(anchor="w")
                detail = f"GPS: {truck.get('latitude', 0.0):.4f}, {truck.get('longitude', 0.0):.4f}  ·  Max: {truck.get('max_weight_kg', 0)} kg  ·  Vol: {truck.get('max_volume_m3', 0)} m³"
                ctk.CTkLabel(left, text=detail, font=ctk.CTkFont(size=11), text_color=COLORS["muted"]).pack(anchor="w", pady=(2, 0))

                right = ctk.CTkFrame(row, fg_color="transparent")
                right.pack(side="right", padx=16)
                is_reefer = truck.get("refrigerated", False)
                reefer_text = f"Reefer ({truck.get('min_temp_c', 0)}°C)" if is_reefer else "Ambient"
                ctk.CTkLabel(right, text=reefer_text, font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["teal"] if is_reefer else COLORS["muted"]).pack(side="left", padx=8)
                status = truck.get("status", "idle")
                ctk.CTkLabel(right, text=status.upper(), fg_color=COLORS["teal_soft"] if status == "idle" else COLORS["amber_soft"], text_color=COLORS["teal"] if status == "idle" else COLORS["amber"], corner_radius=6, font=ctk.CTkFont(size=10, weight="bold"), padx=8, pady=4).pack(side="left")

    def _render_shipments_view(self) -> None:
        card = ctk.CTkFrame(self.dynamic_content, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        card.pack(fill="both", expand=True)
        ctk.CTkLabel(card, text="Active Shipments & Waybills", text_color=COLORS["ink"], font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(card, text="Cold-chain pharmaceutical consignments currently in transit or queued.", text_color=COLORS["muted"], font=ctk.CTkFont(size=12)).pack(anchor="w", padx=22, pady=(0, 14))

        list_frame = ctk.CTkScrollableFrame(card, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=16, pady=8)

        if not self.shipments_data:
            ctk.CTkLabel(list_frame, text="No shipments active. Run a simulation scenario or create a shipment.", text_color=COLORS["muted"]).pack(pady=40)
        else:
            for ship in self.shipments_data:
                row = ctk.CTkFrame(list_frame, fg_color="#FAFCFB", corner_radius=10, border_width=1, border_color=COLORS["line"])
                row.pack(fill="x", pady=5, padx=4)
                left = ctk.CTkFrame(row, fg_color="transparent")
                left.pack(side="left", padx=16, pady=12)
                ctk.CTkLabel(left, text=ship.get("cargo_type", "Consignment"), font=ctk.CTkFont(size=14, weight="bold"), text_color=COLORS["ink"]).pack(anchor="w")
                detail = f"ID: {ship.get('id', '—')}  ·  Weight: {ship.get('weight_kg', 0)} kg  ·  Value: ₹{ship.get('value_inr', 0):,}"
                ctk.CTkLabel(left, text=detail, font=ctk.CTkFont(size=11), text_color=COLORS["muted"]).pack(anchor="w", pady=(2, 0))

                right = ctk.CTkFrame(row, fg_color="transparent")
                right.pack(side="right", padx=16)
                status = ship.get("status", "in_transit")
                ctk.CTkLabel(right, text=status.replace("_", " ").upper(), fg_color=COLORS["teal_soft"], text_color=COLORS["teal"], corner_radius=6, font=ctk.CTkFont(size=10, weight="bold"), padx=8, pady=4).pack(side="left")

    def _render_audit_trail_view(self) -> None:
        card = ctk.CTkFrame(self.dynamic_content, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        card.pack(fill="both", expand=True)
        ctk.CTkLabel(card, text="Immutable Incident Audit Trail", text_color=COLORS["ink"], font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(card, text="Phase 17 canonical append-only audit trail verifying state machine transitions.", text_color=COLORS["muted"], font=ctk.CTkFont(size=12)).pack(anchor="w", padx=22, pady=(0, 14))

        list_frame = ctk.CTkScrollableFrame(card, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=16, pady=8)

        if not self.incidents:
            ctk.CTkLabel(list_frame, text="No incident history recorded yet.", text_color=COLORS["muted"]).pack(pady=40)
        else:
            for inc in self.incidents:
                row = ctk.CTkFrame(list_frame, fg_color="#FAFCFB", corner_radius=10, border_width=1, border_color=COLORS["line"])
                row.pack(fill="x", pady=6, padx=4)
                top = ctk.CTkFrame(row, fg_color="transparent")
                top.pack(fill="x", padx=16, pady=(12, 6))
                ctk.CTkLabel(top, text=f"Incident {inc.get('id', '—')}  ·  {inc.get('cargoType', 'Cargo')}", font=ctk.CTkFont(size=13, weight="bold"), text_color=COLORS["ink"]).pack(side="left")
                st = inc.get("state", "UNKNOWN")
                fg, bg = state_color(st)
                ctk.CTkLabel(top, text=state_label(st), fg_color=bg, text_color=fg, corner_radius=6, font=ctk.CTkFont(size=10, weight="bold"), padx=8, pady=2).pack(side="right")

                ctk.CTkLabel(row, text=f"Created: {inc.get('createdAt', '—')}  ·  Minutes to Spoilage: {inc.get('minutesUntilSpoilage', '—')} min", font=ctk.CTkFont(size=11), text_color=COLORS["muted"]).pack(anchor="w", padx=16, pady=(0, 12))

    def _render_dijkstra_view(self) -> None:
        card = ctk.CTkFrame(self.dynamic_content, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        card.pack(fill="both", expand=True, padx=8, pady=4)
        ctk.CTkLabel(card, text="Dijkstra's Congestion-Aware Route Optimizer", text_color=COLORS["ink"], font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(card, text="Solves the fastest rescue corridor factoring in dynamic road network congestion.", text_color=COLORS["muted"], font=ctk.CTkFont(size=12)).pack(anchor="w", padx=22, pady=(0, 14))

        controls = ctk.CTkFrame(card, fg_color="transparent")
        controls.pack(fill="x", padx=22, pady=10)

        ctk.CTkLabel(controls, text="Origin Hub:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 8))
        self.dijkstra_start = ctk.CTkOptionMenu(controls, values=["node_1 (Spear St)", "node_2 (Market St)", "node_3 (Mission Arterial)"], width=180)
        self.dijkstra_start.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(controls, text="Drop-off Terminal:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 8))
        self.dijkstra_end = ctk.CTkOptionMenu(controls, values=["node_7 (Central General Dock 4)", "node_6 (Trauma Vault)", "node_5 (South Basin)"], width=220)
        self.dijkstra_end.pack(side="left", padx=(0, 20))

        ctk.CTkButton(controls, text="Calculate Fastest Path", fg_color=COLORS["teal"], hover_color="#0A5C56", command=self.compute_dijkstra).pack(side="left")

        self.dijkstra_result = ctk.CTkLabel(card, text="Click 'Calculate Fastest Path' to run Dijkstra's algorithm.", font=ctk.CTkFont(size=13), text_color=COLORS["muted"])
        self.dijkstra_result.pack(anchor="w", padx=22, pady=20)

    def compute_dijkstra(self) -> None:
        start = self.dijkstra_start.get().split()[0]
        end = self.dijkstra_end.get().split()[0]
        try:
            res = self.master_app.client.dijkstra_fastest_route(start, end)
            msg = f"Fastest Travel Time: {res.get('total_travel_time_minutes')} min  ·  Distance: {res.get('total_distance_km')} km\nPath: {' → '.join(res.get('path', []))}"
            self.dijkstra_result.configure(text=msg, text_color=COLORS["teal"])
        except ApiError as exc:
            self.dijkstra_result.configure(text=str(exc), text_color=COLORS["red"])

    def _render_porter_view(self) -> None:
        card = ctk.CTkFrame(self.dynamic_content, fg_color=COLORS["card"], corner_radius=12, border_width=1, border_color=COLORS["line"])
        card.pack(fill="both", expand=True, padx=8, pady=4)
        ctk.CTkLabel(card, text="Porter-Style Logistics Fare Estimator", text_color=COLORS["ink"], font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=22, pady=(18, 4))
        ctk.CTkLabel(card, text="Transparent on-demand pricing across Tata Ace, Bolero, and Cold-Chain Reefer fleet.", text_color=COLORS["muted"], font=ctk.CTkFont(size=12)).pack(anchor="w", padx=22, pady=(0, 14))

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.pack(fill="x", padx=22, pady=10)

        ctk.CTkLabel(form, text="Vehicle Category:", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 8))
        self.porter_vehicle = ctk.CTkOptionMenu(form, values=["tata_ultra_reefer", "tata_ace", "bolero_maxi", "tata_407", "eicher_pharma_reefer"], width=200)
        self.porter_vehicle.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(form, text="Distance (km):", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", padx=(0, 8))
        self.porter_dist = ctk.CTkEntry(form, width=80)
        self.porter_dist.insert(0, "25.0")
        self.porter_dist.pack(side="left", padx=(0, 20))

        ctk.CTkButton(form, text="Estimate Fare", fg_color=COLORS["teal"], hover_color="#0A5C56", command=self.compute_porter_fare).pack(side="left")

        self.porter_result = ctk.CTkLabel(card, text="Enter rescue parameters and click 'Estimate Fare'.", font=ctk.CTkFont(size=13), text_color=COLORS["muted"])
        self.porter_result.pack(anchor="w", padx=22, pady=20)

    def compute_porter_fare(self) -> None:
        v_type = self.porter_vehicle.get()
        try:
            dist = float(self.porter_dist.get())
            res = self.master_app.client.estimate_fare(v_type, dist, requires_loading_help=True, is_urgent_rescue=True)
            msg = f"Vehicle: {res.get('vehicleName')}  ·  Base Fare: ₹{res.get('baseFare')}  ·  Distance Charge: ₹{res.get('distanceCharge')}  ·  Telemetry: ₹{res.get('telemetryFee')}\nTOTAL ESTIMATED ESCROW FARE: ₹{res.get('estimatedTotalInr'):,}"
            self.porter_result.configure(text=msg, text_color=COLORS["teal"])
        except Exception as exc:
            self.porter_result.configure(text=str(exc), text_color=COLORS["red"])

    def refresh(self) -> None:
        threading.Thread(target=self._refresh_background, daemon=True).start()

    def _refresh_background(self) -> None:
        try:
            health = self.master_app.client.health()
            incidents = self.master_app.client.incidents()
            trucks = self.master_app.client.trucks()
            shipments = self.master_app.client.shipments()
            self.after(0, lambda: self._render_data(health, incidents, trucks, shipments))
        except ApiError as exc:
            self.after(0, lambda: self.connection.configure(text="● API unavailable", text_color=COLORS["red"]))
            self.after(0, lambda: self.master_app.set_status(str(exc)))

    def _render_data(self, health: dict[str, Any], incidents: list[dict[str, Any]], trucks: list[dict[str, Any]], shipments: list[dict[str, Any]]) -> None:
        self.incidents = incidents
        self.trucks = trucks
        self.shipments_data = shipments
        self.connection.configure(text="● live API", text_color=COLORS["teal"])

        for child in self.stats.winfo_children():
            child.destroy()
        active = [i for i in incidents if i.get("state") not in {"ESCROW_RELEASED", "CANCELLED", "DISPUTED"}]
        released = len([i for i in incidents if i.get("state") == "ESCROW_RELEASED"])
        values = [
            ("Active rescues", str(len(active)), "requires attention"),
            ("Fleet online", str(len(trucks)), "registered trucks"),
            ("Resolved", str(released), "escrow released"),
            ("WebSockets", str(health.get("active_websockets", 0)), "live connections"),
        ]
        for idx, (title, value, detail) in enumerate(values):
            StatCard(self.stats, title, value, detail).grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 6, 0 if idx == 3 else 6))

        self.render_active_view()

    def _add_incident_row(self, incident: dict[str, Any]) -> None:
        state = incident.get("state", "UNKNOWN")
        fg, bg = state_color(state)
        row = ctk.CTkFrame(self.incident_list, fg_color="#FBFCFB", corner_radius=10, border_width=1, border_color=COLORS["line"])
        row.pack(fill="x", padx=2, pady=5)
        row.bind("<Button-1>", lambda _event, item=incident: self.select_incident(item))
        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=12, pady=11)
        ctk.CTkLabel(left, text=incident.get("cargoType", "Cargo rescue"), text_color=COLORS["ink"], font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(left, text=f"{incident.get('id', '—')}  ·  shipment {incident.get('shipmentId', '—')}", text_color=COLORS["muted"], font=ctk.CTkFont(size=10)).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(row, text=state_label(state), text_color=fg, fg_color=bg, corner_radius=8, font=ctk.CTkFont(size=10, weight="bold")).pack(side="right", padx=12, pady=13)

    def select_incident(self, incident: dict[str, Any]) -> None:
        self.selected_incident = incident
        timeline = "Loading audit trail…"
        try:
            events = self.master_app.client.incident_timeline(incident["id"])
            timeline = "\n".join(f"{state_label(e.get('newState') or e.get('type', 'event'))}  {e.get('createdAt', '')[:19]}" for e in events)
        except ApiError as exc:
            timeline = str(exc)
        messagebox.showinfo("Incident audit trail", f"{incident.get('cargoType', 'Cargo rescue')}\n\nState: {state_label(incident.get('state', 'UNKNOWN'))}\n\n{timeline}")

    def draw_map(self) -> None:
        if not hasattr(self, "map_canvas") or not self.map_canvas.winfo_exists():
            return
        canvas = self.map_canvas
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 260)
        for x in range(0, width, 42):
            canvas.create_line(x, 0, x, height, fill="#D7E2DE")
        for y in range(0, height, 42):
            canvas.create_line(0, y, width, y, fill="#D7E2DE")
        canvas.create_line(0, height * .72, width, height * .22, fill="#A9C8C0", width=4)
        for i, incident in enumerate(self.incidents[:8]):
            x = 48 + (i * 71) % max(width - 90, 1)
            y = 62 + (i * 47) % max(height - 110, 1)
            canvas.create_oval(x - 7, y - 7, x + 7, y + 7, fill=COLORS["red"], outline="#FFFFFF", width=2)
            canvas.create_text(x + 12, y - 12, text=f"INC {i + 1}", anchor="w", fill=COLORS["ink"], font=("Segoe UI", 9, "bold"))
        for i, truck in enumerate(self.trucks[:10]):
            x = 70 + (i * 97) % max(width - 120, 1)
            y = 150 + (i * 61) % max(height - 180, 1)
            canvas.create_rectangle(x - 6, y - 6, x + 6, y + 6, fill=COLORS["teal"], outline="#FFFFFF", width=2)
        canvas.create_text(16, height - 18, text="Vector operations view  ·  live coordinates from API", anchor="w", fill=COLORS["muted"], font=("Segoe UI", 9))

    def run_simulation(self) -> None:
        try:
            result = self.master_app.client.run_simulation(self.scenario.get())
            self.master_app.set_status(f"Simulation started: {result.get('scenario')}")
            self.after(700, self.refresh)
        except ApiError as exc:
            self.master_app.set_status(str(exc))


class CargoResQApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CargoResQ — Operations Console")
        self.geometry("1280x800")
        self.minsize(1050, 680)
        self.configure(fg_color=COLORS["canvas"])
        self.client = CargoResQClient()
        self._status = ""
        self.show_login()

    def show_login(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        LoginView(self, self.login).pack(fill="both", expand=True)

    def login(self, endpoint: str, email: str, password: str) -> None:
        self.client = CargoResQClient(endpoint)
        data = self.client.login(email, password)
        self.after(0, lambda: self.show_dashboard(data))

    def show_dashboard(self, _profile: dict[str, Any] | None = None) -> None:
        for child in self.winfo_children():
            child.destroy()
        DashboardView(self).pack(fill="both", expand=True)

    def set_status(self, message: str) -> None:
        self._status = message


def main() -> None:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    app = CargoResQApp()
    app.mainloop()


if __name__ == "__main__":
    main()
