"""
CargoResQ operations console.

The carrier owner's view of their own fleet and of any rescue in progress:
where the trucks actually are, which incidents are open, what help has been
offered and at what price, what the telemetry is saying, and which of their
drivers has raised an alarm.

Run with:
    python -m desktop_app.app
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

try:  # package import when run with -m, flat import under PyInstaller
    from .api_client import ApiError, CargoResQClient, DEFAULT_API_URL
    from .live_feed import LiveFeed
    from .map_panel import MapPanel
    from .theme import (
        COLORS,
        SIDEBAR_WIDTH,
        minutes_label,
        money,
        relative_time,
        severity_color,
        state_color,
        state_label,
    )
    from .widgets import (
        EmptyState,
        KeyValueGrid,
        ListRow,
        StatCard,
        badge,
        body,
        card,
        danger_button,
        eyebrow,
        heading,
        primary_button,
        scrollable,
        secondary_button,
    )
except ImportError:  # pragma: no cover - frozen build
    from api_client import ApiError, CargoResQClient, DEFAULT_API_URL  # pyright: ignore[reportMissingImports]
    from live_feed import LiveFeed  # pyright: ignore[reportMissingImports]
    from map_panel import MapPanel  # pyright: ignore[reportMissingImports]
    from theme import (  # pyright: ignore[reportMissingImports]
        COLORS,
        SIDEBAR_WIDTH,
        minutes_label,
        money,
        relative_time,
        severity_color,
        state_color,
        state_label,
    )
    from widgets import (  # pyright: ignore[reportMissingImports]
        EmptyState,
        KeyValueGrid,
        ListRow,
        StatCard,
        badge,
        body,
        card,
        danger_button,
        eyebrow,
        heading,
        primary_button,
        scrollable,
        secondary_button,
    )

# ============================================================================
# Code Settings
# ============================================================================
# Configure the backend API URL directly here (or override via CARGORESQ_API_URL):
API_ENDPOINT = os.getenv("CARGORESQ_API_URL", "http://localhost:8000")

def _as_float(value: Any) -> Optional[float]:
    """Parse a coordinate that may arrive as a string, or not at all."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


#: The physical progress of a bound rescue: current state -> (next, button).
#:
#: Each step is something a dispatcher actually confirms happened, rather than
#: a status somebody sets. The escrow is moved by the server in step with
#: these, so there is no second machine to drive by hand.
DISPATCH_STEPS = {
    "RESCUE_ACCEPTED": ("DRIVER_EN_ROUTE", "Rescuer dispatched"),
    "DRIVER_EN_ROUTE": ("CARGO_TRANSFER", "Arrived, transferring cargo"),
    "CARGO_TRANSFER": ("RESCUE_IN_TRANSIT", "Loaded, back on the road"),
    "RESCUE_IN_TRANSIT": ("DELIVERED", "Delivered"),
}

#: How the escrow's own states read to an operator.
ESCROW_STATE_TEXT = {
    "INITIATED": "Opened, nothing held yet",
    "ACCEPTED": "Funds held",
    "IN_TRANSIT": "Funds held while the cargo moves",
    "PENDING_VERIFICATION": "Awaiting condition check",
    "RELEASED": "Released to the rescuer",
    "DISPUTED": "Disputed, nothing paid out",
    "CANCELLED": "Cancelled, hold reversed",
}

REFRESH_INTERVAL_MS = 15_000
EVENT_POLL_MS = 400

TABS = [
    ("Command centre", "Live map, open incidents, rescue offers"),
    ("Rescue offers", "Offers made and received"),
    ("Fleet", "Trucks and their current positions"),
    ("Telemetry", "Derived alerts from the fleet"),
    ("SOS", "Driver emergencies, own and nearby"),
    ("Shipments", "Cargo in transit"),
]


#: Accounts created by scripts/seed_demo.py, if it has been run.
#:
#: The seed generates passwords rather than shipping them in source, so the
#: only honest way to offer one-click demo sign-in is to read back what it
#: actually created. When the file is absent -- a real deployment, or a clone
#: nobody has seeded -- the login form is simply blank, which is correct: an
#: empty field is better than a prefilled password that no longer works.
DEMO_CREDENTIALS_PATH = (
    Path(__file__).resolve().parent.parent / "demo_credentials.json"
)


def load_demo_accounts() -> List[Dict[str, str]]:
    try:
        raw = json.loads(DEMO_CREDENTIALS_PATH.read_text(encoding="utf-8"))
        accounts = raw.get("accounts") or []
    except (OSError, ValueError):
        return []
    return [a for a in accounts if a.get("email") and a.get("password")]


class LoginView(ctk.CTkFrame):
    def __init__(self, master: "CargoResQApp") -> None:
        super().__init__(master, fg_color=COLORS["canvas"])
        self.master_app = master
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        panel = card(self, corner_radius=16)
        panel.grid(row=0, column=0)
        panel.grid_columnconfigure(0, weight=1)

        heading(panel, "CargoResQ", size=30).grid(row=0, column=0, padx=44, pady=(36, 2))
        ctk.CTkLabel(
            panel,
            text="OPERATIONS CONSOLE",
            text_color=COLORS["teal"],
            font=ctk.CTkFont(size=11, weight="bold"),
        ).grid(row=1, column=0, padx=44, pady=(0, 26))

        accounts = load_demo_accounts()
        # Drivers sign in on the phone, not here.
        operators = [a for a in accounts if "driver" not in a["label"].lower()]
        first = operators[0] if operators else None

        self.email = self._field(
            panel, "Company email", 2, first["email"] if first else ""
        )
        self.password = self._field(
            panel, "Password", 4, first["password"] if first else "", show="•"
        )

        def _set_account(account: Dict[str, str]) -> None:
            self.email.delete(0, "end")
            self.email.insert(0, account["email"])
            self.password.delete(0, "end")
            self.password.insert(0, account["password"])

        if operators:
            pills_frame = ctk.CTkFrame(panel, fg_color="transparent")
            pills_frame.grid(row=5, column=0, padx=44, pady=(0, 10), sticky="ew")
            ctk.CTkLabel(
                pills_frame, text="Quick login:", text_color=COLORS["muted"],
                font=ctk.CTkFont(size=10, weight="bold"),
            ).pack(side="left", padx=(0, 6))
            for index, account in enumerate(operators[:3]):
                short = account["label"].split(" (")[0]
                ctk.CTkButton(
                    pills_frame,
                    text=short,
                    width=max(80, 7 * len(short)),
                    height=24,
                    fg_color="#0F766E" if index == 0 else "#1E293B",
                    hover_color="#115E59" if index == 0 else "#334155",
                    font=ctk.CTkFont(size=10),
                    command=lambda a=account: _set_account(a),
                ).pack(side="left", padx=2)
        self.status = ctk.CTkLabel(
            panel, text="", text_color=COLORS["red"], wraplength=380,
            font=ctk.CTkFont(size=11),
        )
        self.status.grid(row=6, column=0, padx=44, pady=(4, 0))

        self.button = primary_button(panel, "Sign in", self.submit, width=390, height=42)
        self.button.grid(row=7, column=0, padx=44, pady=(14, 8))

        import socket
        lan_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        ctk.CTkLabel(
            panel,
            text=f"Backend: {API_ENDPOINT}  |  Driver app IP: http://{lan_ip}:8000",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10),
        ).grid(row=8, column=0, padx=44, pady=(0, 4))

        ctk.CTkLabel(
            panel,
            text=(
                f"{len(accounts)} demo accounts seeded - pick one above"
                if accounts
                else "No demo data yet - run: python scripts/seed_demo.py"
            ),
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10),
        ).grid(row=9, column=0, padx=44, pady=(0, 24))

        self.email.bind("<Return>", lambda _e: self.submit())
        self.password.bind("<Return>", lambda _e: self.submit())

    def _field(self, master, label, row, initial, show=None):
        ctk.CTkLabel(
            master, text=label, anchor="w", text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
        ).grid(row=row, column=0, sticky="ew", padx=44, pady=(0, 4))
        entry = ctk.CTkEntry(
            master, width=390, height=38, border_color=COLORS["line"],
            fg_color="#FAFCFB", show=show or "",
        )
        entry.grid(row=row + 1, column=0, padx=44, pady=(0, 10))
        if initial:
            entry.insert(0, initial)
        return entry

    def submit(self) -> None:
        email = self.email.get().strip()
        password = self.password.get()
        if not email or not password:
            self.status.configure(text="Enter your email and password.")
            return

        self.button.configure(state="disabled", text="Signing in...")
        self.status.configure(text="")

        def work():
            try:
                self.master_app.connect(API_ENDPOINT, email, password)
                self.after(0, self.master_app.show_dashboard)
            except Exception as exc:
                err_msg = str(exc)
                self.after(0, lambda m=err_msg: self._on_error(m))

        threading.Thread(target=work, daemon=True).start()

    def _on_error(self, message: str) -> None:
        try:
            if self.winfo_exists():
                self.status.configure(text=message)
                if hasattr(self, "button") and self.button.winfo_exists():
                    self.button.configure(state="normal", text="Sign in")
        except Exception:
            pass


class DashboardView(ctk.CTkFrame):
    def __init__(self, master: "CargoResQApp") -> None:
        super().__init__(master, fg_color=COLORS["canvas"])
        self.master_app = master
        self.active_tab = "Command centre"

        # Server state, refreshed on a timer and by live events.
        self.incidents: List[Dict[str, Any]] = []
        self.fleet: List[Dict[str, Any]] = []
        self.shipments: List[Dict[str, Any]] = []
        self.alerts: List[Dict[str, Any]] = []
        self.own_sos: List[Dict[str, Any]] = []
        self.nearby_sos: List[Dict[str, Any]] = []
        self.inbox: List[Dict[str, Any]] = []
        self.selected_incident_id: Optional[str] = None
        self.selected_offers: List[Dict[str, Any]] = []

        self.sidebar_buttons: Dict[str, ctk.CTkButton] = {}
        self.tab_frames: Dict[str, ctk.CTkFrame] = {}
        self.stat_cards: List[StatCard] = []
        self.map_panel: Optional[MapPanel] = None
        self.feed: Optional[LiveFeed] = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._data_signatures: Dict[str, Any] = {}

        self._build_sidebar()
        self._build_main()
        self.start_live_feed()
        self.refresh()
        self.after(EVENT_POLL_MS, self._poll_events)
        self.after(REFRESH_INTERVAL_MS, self._auto_refresh)

    # -- chrome ----------------------------------------------------------
    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=COLORS["ink"]
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar, text="CargoResQ", text_color="#FFFFFF",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(28, 2))
        self.company_label = ctk.CTkLabel(
            sidebar, text="", text_color=COLORS["sidebar_eyebrow"],
            font=ctk.CTkFont(size=10, weight="bold"), wraplength=190, justify="left",
        )
        self.company_label.pack(anchor="w", padx=22, pady=(0, 26))

        for label, _hint in TABS:
            button = ctk.CTkButton(
                sidebar, text=label, anchor="w", height=38,
                fg_color=COLORS["sidebar_active"] if label == self.active_tab else "transparent",
                hover_color=COLORS["sidebar_active"],
                text_color=(
                    COLORS["sidebar_text_active"] if label == self.active_tab
                    else COLORS["sidebar_text"]
                ),
                font=ctk.CTkFont(size=13),
                command=lambda name=label: self.switch_tab(name),
            )
            button.pack(fill="x", padx=12, pady=2)
            self.sidebar_buttons[label] = button

        ctk.CTkLabel(
            sidebar, text="", text_color=COLORS["sidebar_text"]
        ).pack(expand=True)

        self.feed_status = ctk.CTkLabel(
            sidebar, text="● connecting", text_color=COLORS["sidebar_dim"],
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        self.feed_status.pack(anchor="w", padx=22, pady=(0, 8))

        ctk.CTkButton(
            sidebar, text="Sign out", anchor="w", height=38, fg_color="transparent",
            hover_color=COLORS["sidebar_active"], text_color=COLORS["sidebar_text"],
            font=ctk.CTkFont(size=12), command=self.master_app.sign_out,
        ).pack(fill="x", padx=12, pady=(0, 18))

    def _build_main(self) -> None:
        self.main = ctk.CTkFrame(self, fg_color=COLORS["canvas"], corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew", padx=26, pady=22)
        self.main.grid_columnconfigure(0, weight=1)
        self.main.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self.main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w")
        self.view_title = heading(titles, self.active_tab, size=26)
        self.view_title.pack(anchor="w")
        self.view_hint = body(titles, TABS[0][1], size=11)
        self.view_hint.pack(anchor="w", pady=(2, 0))

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=0, column=1, sticky="e")
        self.updated_label = body(controls, "", size=10)
        self.updated_label.pack(side="left", padx=(0, 12))
        secondary_button(controls, "Refresh", self.refresh, width=88).pack(side="left")

        self.stats = ctk.CTkFrame(self.main, fg_color="transparent")
        self.stats.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        for i in range(4):
            self.stats.grid_columnconfigure(i, weight=1)
            sc = StatCard(self.stats, "", "0", "")
            sc.grid(
                row=0, column=i, sticky="ew",
                padx=(0 if i == 0 else 6, 0 if i == 3 else 6),
            )
            self.stat_cards.append(sc)

        self.content = ctk.CTkFrame(self.main, fg_color="transparent")
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        # Pre-build all tabs once so tab switching is instantaneous
        self._build_command_centre()
        self._build_offers()
        self._build_fleet()
        self._build_telemetry()
        self._build_sos()
        self._build_shipments()

        self.show_tab(self.active_tab)

    def switch_tab(self, name: str) -> None:
        self.active_tab = name
        self.view_title.configure(text=name)
        self.view_hint.configure(text=dict(TABS).get(name, ""))
        for label, button in self.sidebar_buttons.items():
            active = label == name
            button.configure(
                fg_color=COLORS["sidebar_active"] if active else "transparent",
                text_color=(
                    COLORS["sidebar_text_active"] if active else COLORS["sidebar_text"]
                ),
            )
        self.show_tab(name)

    # -- data ------------------------------------------------------------
    def refresh(self) -> None:
        threading.Thread(target=self._fetch, daemon=True).start()

    def _auto_refresh(self) -> None:
        self.refresh()
        self.after(REFRESH_INTERVAL_MS, self._auto_refresh)

    def _fetch(self) -> None:
        client = self.master_app.client
        payload: Dict[str, Any] = {}
        errors: List[str] = []

        # Each call is independent: one failing endpoint should not blank the
        # whole console.
        for key, call in [
            ("incidents", client.incidents),
            ("fleet", client.fleet_live),
            ("shipments", client.shipments),
            ("alerts", lambda: client.alerts("OPEN")),
            ("own_sos", client.active_sos),
            ("nearby_sos", client.nearby_sos),
            ("inbox", client.offer_inbox),
        ]:
            try:
                payload[key] = call()
            except ApiError as exc:
                payload[key] = []
                errors.append(f"{key}: {exc}")

        self._safe_after(lambda: self._apply(payload, errors))

    def _apply(self, payload: Dict[str, Any], errors: List[str]) -> None:
        self.incidents = payload.get("incidents", [])
        self.fleet = payload.get("fleet", [])
        self.shipments = payload.get("shipments", [])
        self.alerts = payload.get("alerts", [])
        self.own_sos = payload.get("own_sos", [])
        self.nearby_sos = payload.get("nearby_sos", [])
        self.inbox = payload.get("inbox", [])

        from datetime import datetime

        self.updated_label.configure(
            text=f"Updated {datetime.now().strftime('%H:%M:%S')}"
        )
        if errors:
            self.master_app.set_status(errors[0])
        self._render_stats()
        self._update_tab(self.active_tab)

    def _render_stats(self) -> None:
        terminal = {"ESCROW_RELEASED", "CANCELLED", "DISPUTED"}
        open_incidents = [i for i in self.incidents if i.get("state") not in terminal]
        critical = [a for a in self.alerts if a.get("severity") == "CRITICAL"]
        live_trucks = [t for t in self.fleet if t.get("positionIsLive")]

        cards_data = [
            (
                "Open incidents",
                str(len(open_incidents)),
                "needing a rescue" if open_incidents else "all clear",
                COLORS["red"] if open_incidents else None,
            ),
            (
                "Fleet reporting",
                f"{len(live_trucks)}/{len(self.fleet)}",
                "sending live positions",
                None if live_trucks == self.fleet else COLORS["amber"],
            ),
            (
                "Open alerts",
                str(len(self.alerts)),
                f"{len(critical)} critical" if critical else "nothing urgent",
                COLORS["red"] if critical else None,
            ),
            (
                "Active SOS",
                str(len(self.own_sos)),
                f"+{len(self.nearby_sos)} nearby on network",
                COLORS["red"] if self.own_sos else None,
            ),
        ]
        for index, (title, value, detail, accent) in enumerate(cards_data):
            if index < len(self.stat_cards):
                self.stat_cards[index].update_card(title, value, detail, accent)

    # -- tab management --------------------------------------------------
    def show_tab(self, name: str) -> None:
        for tab_name, frame in self.tab_frames.items():
            if tab_name != name:
                frame.grid_remove()

        if name not in self.tab_frames:
            builders: Dict[str, Callable[[], None]] = {
                "Command centre": self._build_command_centre,
                "Rescue offers": self._build_offers,
                "Fleet": self._build_fleet,
                "Telemetry": self._build_telemetry,
                "SOS": self._build_sos,
                "Shipments": self._build_shipments,
            }
            builder = builders.get(name, self._build_command_centre)
            builder()

        self.tab_frames[name].grid(row=0, column=0, sticky="nsew")
        self._update_tab(name)

    def _tab_signature(self, name: str) -> Any:
        if name == "Command centre":
            return (
                len(self.incidents),
                tuple(
                    (i.get("id"), i.get("state"), i.get("minutesUntilSpoilage"))
                    for i in self.incidents
                ),
                self.selected_incident_id,
                len(self.selected_offers),
                tuple(
                    (o.get("id"), o.get("state"), o.get("priceTotalInr"))
                    for o in self.selected_offers
                ),
            )
        elif name == "Rescue offers":
            return (
                len(self.inbox),
                tuple(
                    (o.get("id"), o.get("state"), o.get("payoutInr"))
                    for o in self.inbox
                ),
            )
        elif name == "Fleet":
            return (
                len(self.fleet),
                tuple(
                    (
                        t.get("truckId") or t.get("id"),
                        t.get("status"),
                        t.get("positionIsLive"),
                        t.get("latitude"),
                        t.get("longitude"),
                    )
                    for t in self.fleet
                ),
            )
        elif name == "Telemetry":
            return (
                len(self.alerts),
                tuple(
                    (a.get("id"), a.get("status"), a.get("severity"), a.get("occurrenceCount"))
                    for a in self.alerts
                ),
            )
        elif name == "SOS":
            return (
                len(self.own_sos),
                len(self.nearby_sos),
                tuple((s.get("id"), s.get("status")) for s in self.own_sos),
                tuple((s.get("id"), s.get("status")) for s in self.nearby_sos),
            )
        elif name == "Shipments":
            return (
                len(self.shipments),
                tuple((s.get("id"), s.get("status")) for s in self.shipments),
            )
        return None

    def _update_tab(self, name: str, force: bool = False) -> None:
        if name not in self.tab_frames:
            return
        sig = self._tab_signature(name)
        if not force and self._data_signatures.get(name) == sig:
            return
        self._data_signatures[name] = sig

        updaters: Dict[str, Callable[[], None]] = {
            "Command centre": self._update_command_centre,
            "Rescue offers": self._update_offers,
            "Fleet": self._update_fleet,
            "Telemetry": self._update_telemetry,
            "SOS": self._update_sos,
            "Shipments": self._update_shipments,
        }
        updater = updaters.get(name, self._update_command_centre)
        updater()

    def render(self) -> None:
        self._update_tab(self.active_tab, force=True)

    # ---- command centre ----
    def _build_command_centre(self) -> None:
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.grid_columnconfigure(0, weight=2, uniform="cc")
        wrap.grid_columnconfigure(1, weight=3, uniform="cc")
        wrap.grid_rowconfigure(0, weight=1)

        left = card(wrap)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(left, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))
        header.grid_columnconfigure(0, weight=1)
        heading(header, "Incidents").grid(row=0, column=0, sticky="w")
        secondary_button(
            header, "Report breakdown", self._open_report_breakdown, width=140
        ).grid(row=0, column=1, sticky="e")

        self.cc_incidents_listing = scrollable(left)
        self.cc_incidents_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 14))

        right = ctk.CTkFrame(wrap, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(0, weight=3)
        right.grid_rowconfigure(1, weight=2)

        self.cc_map_panel = MapPanel(right, height=320)
        self.cc_map_panel.grid(row=0, column=0, sticky="nsew", pady=(0, 10))

        self.detail_card = card(right)
        self.detail_card.grid(row=1, column=0, sticky="nsew")

        self.tab_frames["Command centre"] = wrap

    def _update_command_centre(self) -> None:
        for child in self.cc_incidents_listing.winfo_children():
            child.destroy()

        if not self.incidents:
            EmptyState(
                self.cc_incidents_listing,
                "No incidents",
                "Nothing has broken down. Incidents appear here the moment a "
                "driver reports a breakdown from the app.",
            ).pack(fill="x")
        else:
            for incident in self.incidents:
                state = incident.get("state", "")
                ListRow(
                    self.cc_incidents_listing,
                    incident.get("cargoType") or "Cargo rescue",
                    f"{incident.get('id')}  |  {state_label(state)}"
                    + (
                        f"  |  {minutes_label(incident.get('minutesUntilSpoilage'))} left"
                        if incident.get("minutesUntilSpoilage") is not None
                        else ""
                    ),
                    state_label(state),
                    state_color(state),
                    on_click=lambda i=incident: self.select_incident(i["id"]),
                    selected=incident["id"] == self.selected_incident_id,
                ).pack(fill="x", pady=4, padx=2)

        self.map_panel = self.cc_map_panel
        self._draw_map(self.cc_map_panel)
        self._render_incident_detail()

    def _draw_map(self, target_panel: Optional[MapPanel] = None) -> None:
        panel = target_panel or getattr(self, "map_panel", None)
        if panel is None:
            return
        specs: Dict[str, Dict[str, Any]] = {}
        positions = []

        for truck in self.fleet:
            lat, lng = truck.get("latitude"), truck.get("longitude")
            if lat is None or lng is None:
                continue
            live = truck.get("positionIsLive")
            suspect = truck.get("positionSuspect")
            t_id = str(truck.get("truckId") or truck.get("id"))
            label = str(truck.get("registrationNumber", t_id))
            if suspect:
                # Marked on the pin itself. A position the system does not
                # believe should not look identical to one it does.
                label += "  (unverified)"
            specs[f"truck:{t_id}"] = {
                "lat": float(lat),
                "lng": float(lng),
                "text": label,
                "kind": "suspect" if suspect else ("own_truck" if live else "candidate"),
                "detail": f"{truck.get('status')} | {relative_time(truck.get('lastSeenAt'))}",
            }
            positions.append((float(lat), float(lng)))

        for incident in self.incidents:
            if incident.get("state") in {"ESCROW_RELEASED", "CANCELLED"}:
                continue
            lat, lng = incident.get("lat"), incident.get("lng")
            if lat is None or lng is None:
                continue
            specs[f"incident:{incident['id']}"] = {
                "lat": float(lat),
                "lng": float(lng),
                "text": str(incident.get("cargoType") or "Incident"),
                "kind": "incident",
            }
            positions.append((float(lat), float(lng)))

        for alert in self.own_sos:
            alat = alert.get("latitude")
            alng = alert.get("longitude")
            if alat is None or alng is None:
                continue
            specs[f"sos:{alert['id']}"] = {
                "lat": float(alat),
                "lng": float(alng),
                "text": f"SOS {alert.get('category')}",
                "kind": "sos",
            }
            positions.append((float(alat), float(alng)))

        for alert in self.nearby_sos:
            alat = alert.get("approxLat") or alert.get("latitude")
            alng = alert.get("approxLng") or alert.get("longitude")
            if alat is None or alng is None:
                continue
            a_id = alert.get("id") or alert.get("alertId") or "nearby"
            specs[f"sos_nearby:{a_id}"] = {
                "lat": float(alat),
                "lng": float(alng),
                "text": f"Nearby SOS {alert.get('category', '')}",
                "kind": "sos",
            }
            positions.append((float(alat), float(alng)))

        panel.sync_markers(specs)

        pos_tuple = tuple(sorted(positions))
        if getattr(panel, "_last_positions", None) != pos_tuple:
            panel.fit_to_markers(positions)
            panel._last_positions = pos_tuple

        stale = len([t for t in self.fleet if not t.get("positionIsLive")])
        panel.set_status(
            f"{len(positions)} positions  |  {stale} truck(s) not reporting"
            if positions
            else "No positions yet -- trucks appear once the driver app sends telemetry"
        )

    def _render_incident_detail(self) -> None:
        if not hasattr(self, "detail_card"):
            return
        for child in self.detail_card.winfo_children():
            child.destroy()

        if not self.selected_incident_id:
            EmptyState(
                self.detail_card,
                "Select an incident",
                "Pick an incident to see its rescue offers, price and audit trail.",
            ).pack(fill="both", expand=True)
            return

        incident = next(
            (i for i in self.incidents if i["id"] == self.selected_incident_id), None
        )
        if incident is None:
            EmptyState(self.detail_card, "Incident no longer listed").pack(fill="both", expand=True)
            return

        container = scrollable(self.detail_card)
        container.pack(fill="both", expand=True, padx=14, pady=14)

        top = ctk.CTkFrame(container, fg_color="transparent")
        top.pack(fill="x")
        heading(top, incident.get("cargoType") or "Cargo rescue").pack(side="left")
        fg, bg = state_color(incident.get("state", ""))
        badge(top, state_label(incident.get("state", "")), fg, bg).pack(side="right")

        KeyValueGrid(
            container,
            [
                ("Incident", incident.get("id", "--")),
                ("Shipment", incident.get("shipmentId", "--")),
                ("Position", f"{incident.get('lat'):.4f}, {incident.get('lng'):.4f}"),
                (
                    "Time to spoilage",
                    minutes_label(incident.get("minutesUntilSpoilage")),
                ),
                ("Assigned truck", incident.get("assignedTruckId") or "none yet"),
            ],
        ).pack(fill="x", pady=(12, 14))

        actions = ctk.CTkFrame(container, fg_color="transparent")
        actions.pack(fill="x", pady=(0, 12))
        state = incident.get("state")

        if state in {"BREAKDOWN_REPORTED", "TRIAGING"}:
            primary_button(
                actions, "Find rescuers", lambda: self._find_rescuers(incident)
            ).pack(side="left", padx=(0, 8))
        elif state == "MATCHING":
            primary_button(
                actions, "Send offers", lambda: self._send_offers(incident)
            ).pack(side="left", padx=(0, 8))
        elif state in DISPATCH_STEPS:
            # Once a rescue is bound the job is physical: the truck sets off,
            # the cargo is moved across, it is driven on, it arrives. Each of
            # these is a real thing a dispatcher confirms, and the escrow
            # follows along behind them.
            next_state, label = DISPATCH_STEPS[state]
            primary_button(
                actions,
                label,
                lambda s=next_state: self._advance_incident(incident, s),
            ).pack(side="left", padx=(0, 8))
        elif state == "DELIVERED":
            primary_button(
                actions,
                "Verify and release payment",
                lambda: self._settle_incident(incident),
            ).pack(side="left", padx=(0, 8))

        secondary_button(
            actions, "Audit trail", lambda: self._show_timeline(incident["id"]), width=110
        ).pack(side="left", padx=(0, 8))

        escrow = incident.get("escrow")
        if escrow:
            secondary_button(
                actions,
                "Escrow",
                lambda e=escrow: self._show_escrow({"escrowId": e["id"]}),
                width=90,
            ).pack(side="left")

        # -- where the money is -----------------------------------------
        if escrow:
            self._render_escrow_strip(container, escrow, state)
        else:
            self._render_escrow_pending(container)

        eyebrow(container, "Rescue offers").pack(anchor="w", pady=(6, 6))
        if not self.selected_offers:
            body(
                container,
                "No offers yet. Use 'Send offers' to ask compatible carriers nearby.",
                size=11,
            ).pack(anchor="w")
        for offer in self.selected_offers:
            self._render_offer_card(container, offer)


    def _render_escrow_pending(self, master: Any) -> None:
        """No escrow yet, and saying so is better than showing nothing.

        An operator looking at a rescue with no money line has to guess
        whether the funds are held, missing, or simply not due yet. The
        answer is the third one, and it is worth stating: the hold is placed
        by the handshake, at the moment they confirm a carrier.
        """
        awaiting = [
            o for o in self.selected_offers if o.get("state") == "CARRIER_ACCEPTED"
        ]
        strip = card(master, fg_color=COLORS["row"])
        strip.pack(fill="x", pady=(4, 10))

        row = ctk.CTkFrame(strip, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(10, 2))
        heading(row, "No funds held yet", size=14).pack(side="left")
        badge(row, "Not opened", COLORS["muted"], COLORS["line"]).pack(side="right")

        if awaiting:
            cheapest = min(awaiting, key=lambda o: o.get("priceTotalInr") or 0)
            detail = (
                f"Confirming a rescuer opens the escrow and holds "
                f"{money(cheapest.get('priceTotalInr'))} until delivery is verified."
            )
        else:
            detail = (
                "The escrow opens when a carrier accepts and you confirm them. "
                "Until both sides agree, no money moves."
            )
        body(strip, detail, size=11).pack(anchor="w", padx=14, pady=(0, 10))

    def _render_escrow_strip(
        self, master: Any, escrow: Dict[str, Any], incident_state: str
    ) -> None:
        """Where the money currently sits, in one line.

        Shown next to the rescue rather than buried behind a button, because
        "is my money safe" is the question a cargo owner is actually asking
        while they wait.
        """
        state = escrow.get("state", "")
        strip = card(master, fg_color=COLORS["row"])
        strip.pack(fill="x", pady=(4, 10))

        row = ctk.CTkFrame(strip, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(10, 2))
        heading(row, money(escrow.get("amountInr")), size=14).pack(side="left")
        fg, bg = state_color(state)
        badge(row, state_label(state), fg, bg).pack(side="right")

        detail = ESCROW_STATE_TEXT.get(state, state_label(state))
        payout = escrow.get("carrierPayoutInr")
        if payout is not None:
            detail += f"  ·  rescuer receives {money(payout)}"
        body(strip, detail, size=11).pack(anchor="w", padx=14, pady=(0, 2))

        if escrow.get("stateReason"):
            body(
                strip,
                escrow["stateReason"],
                size=10,
                color=COLORS["amber"] if state != "RELEASED" else COLORS["teal"],
            ).pack(anchor="w", padx=14)

        if incident_state == "DELIVERED" and state == "PENDING_VERIFICATION":
            body(
                strip,
                "Release checks the recorded temperature log. If the cargo went "
                "out of range it will be disputed instead of paid.",
                size=10,
            ).pack(anchor="w", padx=14, pady=(2, 10))
        else:
            body(strip, "", size=2).pack()

    def _advance_incident(self, incident: Dict[str, Any], new_state: str) -> None:
        """Move the rescue on one step. The escrow follows server-side."""
        self._run(
            lambda: self.master_app.client.advance_incident(incident["id"], new_state),
            f"Rescue moved to {state_label(new_state)}.",
        )

    def _settle_incident(self, incident: Dict[str, Any]) -> None:
        """Verify the condition log and settle the escrow accordingly."""
        escrow = incident.get("escrow")
        if not escrow:
            messagebox.showinfo(
                "CargoResQ",
                "There is no escrow on this rescue, so there is nothing to settle.",
            )
            return

        def work() -> None:
            try:
                result = self.master_app.client.escrow_verify(escrow["id"])
            except ApiError as exc:
                self._safe_after(lambda: messagebox.showerror("CargoResQ", str(exc)))
                return

            verdict = (result.get("verification") or {}).get("verdict", "")
            reason = (result.get("verification") or {}).get("reason", "")

            gap = "\n\n"

            def report() -> None:
                if verdict == "PASS":
                    messagebox.showinfo(
                        "Payment released",
                        "The condition log confirms the cargo arrived in spec."
                        + gap
                        + reason,
                    )
                elif verdict == "FAIL":
                    messagebox.showwarning(
                        "Disputed",
                        "The cargo went outside its agreed range, so the payment "
                        "has been disputed rather than released." + gap + reason,
                    )
                else:
                    # Neither party is favoured when there is no evidence.
                    messagebox.showwarning(
                        "Not enough evidence",
                        "There is not enough recorded condition data to settle "
                        "this automatically." + gap + reason + gap
                        + "The funds stay held pending manual review.",
                    )
                self.refresh()
                if self.selected_incident_id:
                    self.select_incident(self.selected_incident_id)

            self._safe_after(report)

        threading.Thread(target=work, daemon=True).start()

    def _render_offer_card(self, master: Any, offer: Dict[str, Any]) -> None:
        holder = card(master, fg_color=COLORS["row"])
        holder.pack(fill="x", pady=5)

        row = ctk.CTkFrame(holder, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 4))
        heading(row, money(offer.get("priceTotalInr")), size=15).pack(side="left")
        fg, bg = state_color(offer.get("state", ""))
        badge(row, state_label(offer.get("state", "")), fg, bg).pack(side="right")

        meta = (
            f"ETA {minutes_label(offer.get('etaMinutes'))}  |  "
            f"{offer.get('distanceKm', 0):.1f} km  |  "
            f"score {offer.get('rescueScore', 0):.0f}"
        )
        body(holder, meta, size=11).pack(anchor="w", padx=14)

        # The carrier's reputation, shown at the moment of the decision --
        # this is who the owner is about to trust with their cargo.
        reputation = offer.get("carrierReputation") or {}
        if reputation:
            if reputation.get("isNewCounterparty"):
                rep_text = "New to the network -- no completed rescues yet"
                rep_color = COLORS["amber"]
            else:
                stars = reputation.get("averageStars")
                rep_text = (
                    f"Trust {reputation.get('trustScore')}  |  "
                    f"{reputation.get('rescuesCompleted')} rescues"
                    + (f"  |  {stars:.1f}/5 from {reputation.get('ratingCount')}" if stars else "")
                )
                rep_color = COLORS["teal"] if reputation.get("trustScore", 0) >= 80 else COLORS["amber"]
            body(holder, rep_text, size=11, color=rep_color).pack(anchor="w", padx=14, pady=(4, 0))

            for signal in (reputation.get("signals") or [])[:3]:
                mark = {"positive": "+", "negative": "!", "neutral": "-"}.get(
                    signal.get("kind"), "-"
                )
                body(holder, f"  {mark} {signal.get('label')}", size=10).pack(
                    anchor="w", padx=14
                )

        for reason in (offer.get("scoreReasons") or [])[:3]:
            body(holder, f"  • {reason}", size=10).pack(anchor="w", padx=14)

        buttons = ctk.CTkFrame(holder, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(10, 12))

        if offer.get("state") in {"PENDING", "CARRIER_ACCEPTED"}:
            label = (
                "Confirm rescue" if offer.get("carrierAccepted") else "Pre-confirm"
            )
            primary_button(
                buttons, label, lambda o=offer: self._confirm_offer(o), height=34
            ).pack(side="left", padx=(0, 8))
            secondary_button(
                buttons, "Withdraw", lambda o=offer: self._withdraw_offer(o), width=96
            ).pack(side="left")
        elif offer.get("state") == "BOUND":
            body(
                buttons,
                f"Bound. Escrow {offer.get('escrowId')} holding "
                f"{money(offer.get('priceTotalInr'))}.",
                size=11,
                color=COLORS["teal"],
            ).pack(side="left")
            secondary_button(
                buttons, "Escrow", lambda o=offer: self._show_escrow(o), width=90
            ).pack(side="right")

    # ---- offers tab ----
    def _build_offers(self) -> None:
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.grid_columnconfigure(0, weight=1, uniform="o")
        wrap.grid_columnconfigure(1, weight=1, uniform="o")
        wrap.grid_rowconfigure(0, weight=1)

        inbox = card(wrap)
        inbox.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        inbox.grid_rowconfigure(1, weight=1)
        inbox.grid_columnconfigure(0, weight=1)
        heading(inbox, "Offers to us").grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        body(
            inbox,
            "Rescues other carriers have asked us to perform. Accepting shows "
            "what we would earn; the customer's own price is theirs.",
            size=11,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(34, 0))

        self.offers_inbox_listing = scrollable(inbox)
        self.offers_inbox_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(12, 14))

        history = card(wrap)
        history.grid(row=0, column=1, sticky="nsew")
        history.grid_rowconfigure(1, weight=1)
        history.grid_columnconfigure(0, weight=1)
        heading(history, "Recent activity").grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 8)
        )
        self.offers_history_listing = scrollable(history)
        self.offers_history_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        self.tab_frames["Rescue offers"] = wrap

    def _update_offers(self) -> None:
        for child in self.offers_inbox_listing.winfo_children():
            child.destroy()

        live = [o for o in self.inbox if o.get("state") in {"PENDING", "OWNER_CONFIRMED"}]
        if not live:
            EmptyState(
                self.offers_inbox_listing,
                "No open requests",
                "When a nearby carrier's truck breaks down and one of ours fits "
                "the cargo, the request appears here.",
            ).pack(fill="x")
        for offer in live:
            holder = card(self.offers_inbox_listing, fg_color=COLORS["row"])
            holder.pack(fill="x", pady=5)
            row = ctk.CTkFrame(holder, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(12, 2))
            heading(row, f"Earn {money(offer.get('payoutInr'))}", size=15).pack(side="left")
            fg, bg = state_color(offer.get("state", ""))
            badge(row, state_label(offer.get("state", "")), fg, bg).pack(side="right")
            body(
                holder,
                f"ETA {minutes_label(offer.get('etaMinutes'))}  |  "
                f"{offer.get('distanceKm', 0):.1f} km away",
                size=11,
            ).pack(anchor="w", padx=14, pady=(2, 0))

            buttons = ctk.CTkFrame(holder, fg_color="transparent")
            buttons.pack(fill="x", padx=14, pady=(10, 12))
            primary_button(
                buttons, "Accept", lambda o=offer: self._accept_offer(o), height=34
            ).pack(side="left", padx=(0, 8))
            secondary_button(
                buttons, "Decline", lambda o=offer: self._decline_offer(o), width=90
            ).pack(side="left")

        for child in self.offers_history_listing.winfo_children():
            child.destroy()

        closed = [o for o in self.inbox if o.get("state") not in {"PENDING", "OWNER_CONFIRMED"}]
        if not closed:
            EmptyState(self.offers_history_listing, "Nothing yet").pack(fill="x")
        for offer in closed[:25]:
            ListRow(
                self.offers_history_listing,
                money(offer.get("payoutInr")),
                f"{offer.get('id')}  |  {relative_time(offer.get('createdAt'))}",
                state_label(offer.get("state", "")),
                state_color(offer.get("state", "")),
            ).pack(fill="x", pady=4, padx=2)

    # ---- fleet ----
    def _build_fleet(self) -> None:
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.grid_columnconfigure(0, weight=2, uniform="f")
        wrap.grid_columnconfigure(1, weight=3, uniform="f")
        wrap.grid_rowconfigure(0, weight=1)

        listing_card = card(wrap)
        listing_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        listing_card.grid_rowconfigure(1, weight=1)
        listing_card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(listing_card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 6))
        head.grid_columnconfigure(0, weight=1)
        heading(head, "Trucks").grid(row=0, column=0, sticky="w")
        secondary_button(head, "Add truck", self._open_add_truck, width=98).grid(
            row=0, column=1, sticky="e"
        )

        self.fleet_listing = scrollable(listing_card)
        self.fleet_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(4, 14))

        self.fleet_map_panel = MapPanel(wrap, height=420)
        self.fleet_map_panel.grid(row=0, column=1, sticky="nsew")
        self.fleet_map_panel.set_title("Fleet positions", "OpenStreetMap")

        self.tab_frames["Fleet"] = wrap

    def _update_fleet(self) -> None:
        for child in self.fleet_listing.winfo_children():
            child.destroy()

        if not self.fleet:
            EmptyState(
                self.fleet_listing, "No trucks registered", "Add a truck to start tracking it."
            ).pack(fill="x")
        for truck in self.fleet:
            live = truck.get("positionIsLive")
            subtitle = (
                f"{truck.get('latitude', 0):.4f}, {truck.get('longitude', 0):.4f}  |  "
                + (
                    f"live {relative_time(truck.get('lastSeenAt'))}"
                    if live
                    else "registered position only"
                )
            )
            ListRow(
                self.fleet_listing,
                truck.get("registrationNumber", truck["truckId"]),
                subtitle,
                truck.get("status", "").replace("_", " "),
                state_color(truck.get("status", "")),
                on_click=lambda t=truck: self._focus_truck(t),
            ).pack(fill="x", pady=4, padx=2)

        self.map_panel = self.fleet_map_panel
        self._draw_map(self.fleet_map_panel)

    def _focus_truck(self, truck: Dict[str, Any]) -> None:
        panel = getattr(self, "fleet_map_panel", None) or getattr(self, "map_panel", None)
        if panel is not None and truck.get("latitude") is not None:
            panel.focus_on(truck["latitude"], truck["longitude"], zoom=13)

    # ---- telemetry ----
    def _build_telemetry(self) -> None:
        holder = card(self.content)
        holder.grid_rowconfigure(1, weight=1)
        holder.grid_columnconfigure(0, weight=1)

        heading(holder, "Open alerts").grid(row=0, column=0, sticky="w", padx=18, pady=(16, 2))
        body(
            holder,
            "Derived from telemetry: a truck that has stopped moving, gone "
            "quiet, jumped impossibly far, or let its cargo out of range.",
            size=11,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(34, 0))

        self.telemetry_listing = scrollable(holder)
        self.telemetry_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(14, 14))

        self.tab_frames["Telemetry"] = holder

    def _update_telemetry(self) -> None:
        for child in self.telemetry_listing.winfo_children():
            child.destroy()

        if not self.alerts:
            EmptyState(
                self.telemetry_listing,
                "Nothing to report",
                "Alerts appear here when the fleet's telemetry says something "
                "is wrong.",
            ).pack(fill="x")

        for alert in self.alerts:
            entry = card(self.telemetry_listing, fg_color=COLORS["row"])
            entry.pack(fill="x", pady=5)

            row = ctk.CTkFrame(entry, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=(12, 2))
            heading(row, alert.get("type", "").replace("_", " ").title(), size=14).pack(
                side="left"
            )
            fg, bg = severity_color(alert.get("severity", ""))
            badge(row, alert.get("severity", ""), fg, bg).pack(side="right")

            detail = alert.get("lastValue") or {}
            summary = ", ".join(f"{k}: {v}" for k, v in list(detail.items())[:3])
            body(entry, summary or "--", size=11).pack(anchor="w", padx=14)
            body(
                entry,
                f"Truck {alert.get('truckId')}  |  opened {relative_time(alert.get('openedAt'))}"
                # The occurrence count is what distinguishes a brief pause from
                # a six-hour standstill; both are one alert row.
                f"  |  seen {alert.get('occurrenceCount')}x",
                size=10,
            ).pack(anchor="w", padx=14, pady=(2, 0))

            buttons = ctk.CTkFrame(entry, fg_color="transparent")
            buttons.pack(fill="x", padx=14, pady=(8, 12))
            secondary_button(
                buttons, "Acknowledge", lambda a=alert: self._ack_alert(a), width=110
            ).pack(side="left", padx=(0, 8))
            secondary_button(
                buttons, "Resolve", lambda a=alert: self._resolve_alert(a), width=90
            ).pack(side="left")

    # ---- sos ----
    def _build_sos(self) -> None:
        wrap = ctk.CTkFrame(self.content, fg_color="transparent")
        wrap.grid_columnconfigure(0, weight=1, uniform="s")
        wrap.grid_columnconfigure(1, weight=1, uniform="s")
        wrap.grid_rowconfigure(0, weight=1)

        own = card(wrap)
        own.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        own.grid_rowconfigure(1, weight=1)
        own.grid_columnconfigure(0, weight=1)
        heading(own, "Our drivers").grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))

        self.sos_own_listing = scrollable(own)
        self.sos_own_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        nearby = card(wrap)
        nearby.grid(row=0, column=1, sticky="nsew")
        nearby.grid_rowconfigure(1, weight=1)
        nearby.grid_columnconfigure(0, weight=1)
        heading(nearby, "Nearby on the network").grid(
            row=0, column=0, sticky="w", padx=18, pady=(16, 2)
        )
        body(
            nearby,
            "Other carriers' drivers close to our trucks. Details are limited "
            "until we offer help.",
            size=11,
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(34, 0))

        self.sos_nearby_listing = scrollable(nearby)
        self.sos_nearby_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(14, 14))

        self.tab_frames["SOS"] = wrap

    def _update_sos(self) -> None:
        for child in self.sos_own_listing.winfo_children():
            child.destroy()
        if not self.own_sos:
            EmptyState(self.sos_own_listing, "No active emergencies", "Everyone is safe.").pack(fill="x")
        for alert in self.own_sos:
            self._render_sos_card(self.sos_own_listing, alert, own=True)

        for child in self.sos_nearby_listing.winfo_children():
            child.destroy()
        if not self.nearby_sos:
            EmptyState(self.sos_nearby_listing, "Nothing nearby").pack(fill="x")
        for alert in self.nearby_sos:
            self._render_sos_card(self.sos_nearby_listing, alert, own=False)


    def _render_responder_board(self, master: Any, alert: Dict[str, Any]) -> None:
        """Who was told, how far out they are, and whether they are coming.

        While waiting on an emergency this is the only question that matters.
        "An alert was broadcast" tells a dispatcher nothing they can act on;
        "three carriers within 12 km, nearest 9 minutes out and already
        moving" tells them whether to keep waiting or escalate.
        """
        responders = alert.get("responders") or []
        en_route = alert.get("respondersEnRoute", 0)

        eyebrow(master, "Help offered").pack(anchor="w", padx=14, pady=(10, 4))

        if not responders:
            body(
                master,
                "No carrier is close enough to be notified yet. Widen the "
                "search or call for outside help.",
                size=11,
                color=COLORS["amber"],
            ).pack(anchor="w", padx=14)
            return

        summary = f"{len(responders)} carrier(s) notified"
        if en_route:
            summary += f"  |  {en_route} on the way"
        body(
            master,
            summary,
            size=11,
            color=COLORS["teal"] if en_route else COLORS["amber"],
        ).pack(anchor="w", padx=14)

        for responder in responders[:5]:
            line = ctk.CTkFrame(master, fg_color="transparent")
            line.pack(fill="x", padx=14, pady=2)

            status = responder.get("status", "NOTIFIED")
            colour = {
                "ON_SCENE": COLORS["teal"],
                "EN_ROUTE": COLORS["teal"],
                "ACKNOWLEDGE": COLORS["amber"],
                "UNABLE": COLORS["muted"],
                "STOOD_DOWN": COLORS["muted"],
            }.get(status, COLORS["muted"])

            eta = responder.get("etaMinutes")
            # An estimate and a commitment are different things, and a
            # dispatcher deciding whether to wait needs to know which is which.
            eta_text = (
                f"~{eta:.0f} min" if eta is not None and responder.get("etaIsEstimate")
                else (f"{eta:.0f} min (stated)" if eta is not None else "no ETA")
            )
            distance = responder.get("distanceKm")
            distance_text = f"{distance:.1f} km" if distance is not None else "--"

            body(
                line,
                f"  {responder.get('companyName', 'Carrier')}"
                f"  ·  {distance_text}  ·  {eta_text}",
                size=11,
                color=COLORS["ink"],
            ).pack(side="left")
            body(
                line,
                status.replace("_", " ").title(),
                size=10,
                color=colour,
            ).pack(side="right")

    def _render_sos_card(self, master: Any, alert: Dict[str, Any], own: bool) -> None:
        entry = card(master, fg_color=COLORS["red_soft"] if own else COLORS["row"])
        entry.pack(fill="x", pady=5)

        row = ctk.CTkFrame(entry, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(12, 2))
        heading(row, alert.get("category", "").replace("_", " ").title(), size=14).pack(
            side="left"
        )
        fg, bg = severity_color(alert.get("severity", ""))
        badge(row, alert.get("severity", ""), fg, bg).pack(side="right")

        if own:
            condition = alert.get("condition") or {}
            bits = []
            if condition.get("personsAffected") is not None:
                bits.append(f"{condition['personsAffected']} affected")
            if condition.get("isConscious") is False:
                bits.append("unconscious")
            if condition.get("severeBleeding"):
                bits.append("severe bleeding")
            if condition.get("note"):
                bits.append(condition["note"])
            body(entry, " | ".join(bits) or "No condition detail", size=11).pack(
                anchor="w", padx=14
            )
            body(
                entry,
                f"{alert.get('latitude'):.4f}, {alert.get('longitude'):.4f}"
                + (f"  |  {alert.get('landmarkNote')}" if alert.get("landmarkNote") else ""),
                size=10,
            ).pack(anchor="w", padx=14, pady=(2, 0))
        else:
            body(
                entry,
                f"~{alert.get('distanceKm', 0):.1f} km away  |  "
                + ", ".join(alert.get("assistanceNeeded") or []),
                size=11,
            ).pack(anchor="w", padx=14)
            body(
                entry,
                f"Approx {alert.get('approxLat')}, {alert.get('approxLng')} "
                f"({alert.get('locationPrecision')})",
                size=10,
            ).pack(anchor="w", padx=14, pady=(2, 0))

        if own:
            self._render_responder_board(entry, alert)

        # Stated on every card. This platform does not call the emergency
        # services, and an operator must never believe otherwise.
        body(
            entry,
            "CargoResQ does not dispatch emergency services. Dial 112 for "
            "police, ambulance or fire.",
            size=9,
            color=COLORS["muted"],
        ).pack(anchor="w", padx=14, pady=(6, 0))

        buttons = ctk.CTkFrame(entry, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(8, 12))
        primary_button(
            buttons, "Emergency Response", lambda a=alert: self.master_app.open_sos_emergency_dialog(a), height=34
        ).pack(side="left", padx=(0, 8))
        if own:
            secondary_button(
                buttons, "Mark resolved", lambda a=alert: self._resolve_sos(a), width=120
            ).pack(side="left")
        else:
            secondary_button(
                buttons, "We can help", lambda a=alert: self._offer_sos_help(a), width=110
            ).pack(side="left", padx=(0, 8))
            secondary_button(
                buttons, "Acknowledge", lambda a=alert: self._ack_sos(a), width=110
            ).pack(side="left")

    # ---- shipments ----
    def _build_shipments(self) -> None:
        holder = card(self.content)
        holder.grid_rowconfigure(1, weight=1)
        holder.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(holder, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        head.grid_columnconfigure(0, weight=1)
        heading(head, "Cargo in transit").grid(row=0, column=0, sticky="w")
        secondary_button(head, "Add shipment", self._open_add_shipment, width=120).grid(
            row=0, column=1, sticky="e"
        )

        self.shipments_listing = scrollable(holder)
        self.shipments_listing.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        self.tab_frames["Shipments"] = holder

    def _update_shipments(self) -> None:
        for child in self.shipments_listing.winfo_children():
            child.destroy()
        if not self.shipments:
            EmptyState(self.shipments_listing, "No shipments", "Add a shipment to track it.").pack(fill="x")
        for shipment in self.shipments:
            conditions = []
            if shipment.get("requires_refrigeration"):
                conditions.append(f"max {shipment.get('required_max_temp_c')}C")
            if shipment.get("is_hazmat"):
                conditions.append("hazmat")
            ListRow(
                self.shipments_listing,
                shipment.get("cargo_type", "Cargo"),
                f"{money(shipment.get('value_inr'))}  |  "
                f"{shipment.get('weight_kg')} kg  |  "
                + (", ".join(conditions) or "no special handling"),
                state_label(shipment.get("status", "")),
                state_color(shipment.get("status", "")),
                on_click=lambda s=shipment: self._show_cold_chain(s),
            ).pack(fill="x", pady=4, padx=2)

    # -- actions ---------------------------------------------------------
    def select_incident(self, incident_id: str) -> None:
        self.selected_incident_id = incident_id
        self.selected_offers = []

        def work():
            try:
                offers = self.master_app.client.incident_offers(incident_id)
            except ApiError:
                offers = []
            self._safe_after(lambda: self._set_offers(offers))

        threading.Thread(target=work, daemon=True).start()
        self._update_tab("Command centre", force=True)

    def _set_offers(self, offers: List[Dict[str, Any]]) -> None:
        self.selected_offers = offers
        if self.active_tab == "Command centre":
            self._render_incident_detail()

    def _safe_after(self, callback: Callable[[], Any]) -> None:
        """Marshal back to the Tk thread, unless the view has gone.

        A refresh started before the operator signed out will still complete
        on its worker thread; scheduling onto a destroyed widget raises.
        """
        try:
            if self.winfo_exists():
                self.after(0, callback)
        except Exception:
            pass

    def _run(self, work: Callable[[], Any], success: str) -> None:
        """Run an API call off the UI thread and report what happened."""

        def task():
            try:
                work()
                self.after(0, lambda: self.master_app.set_status(success))
                self.after(0, self.refresh)
                current_id = self.selected_incident_id
                if current_id:
                    self.after(0, lambda: self.select_incident(current_id))
            except ApiError as exc:
                self.after(0, lambda: messagebox.showerror("CargoResQ", str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _find_rescuers(self, incident: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.advance_incident(incident["id"], "MATCHING"),
            "Searching for compatible trucks.",
        )

    def _send_offers(self, incident: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.fan_out_offers(incident["id"]),
            "Offers sent to nearby compatible carriers.",
        )

    def _confirm_offer(self, offer: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.confirm_offer(offer["id"]),
            "Confirmed. The rescue binds once the carrier also accepts.",
        )

    def _withdraw_offer(self, offer: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.withdraw_offer(offer["id"], "Withdrawn by owner"),
            "Offer withdrawn.",
        )

    def _accept_offer(self, offer: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.accept_offer_as_carrier(offer["id"]),
            "Accepted. The rescue binds once the customer also confirms.",
        )

    def _decline_offer(self, offer: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.decline_offer_as_carrier(offer["id"], "Unavailable"),
            "Declined.",
        )

    def _ack_alert(self, alert: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.acknowledge_alert(alert["id"]), "Alert acknowledged."
        )

    def _resolve_alert(self, alert: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.resolve_alert(alert["id"], "Resolved from console"),
            "Alert resolved.",
        )

    def _ack_sos(self, alert: Dict[str, Any]) -> None:
        sos_id = alert.get("sosId") or alert.get("id")
        if not sos_id:
            return
        self._run(
            lambda: self.master_app.client.acknowledge_sos(sos_id),
            "Acknowledged. The exact location is now visible.",
        )

    def _offer_sos_help(self, alert: Dict[str, Any]) -> None:
        sos_id = alert.get("sosId") or alert.get("id")
        if not sos_id:
            return

        def work():
            self.master_app.client.acknowledge_sos(sos_id)
            self.master_app.client.respond_to_sos(sos_id, "EN_ROUTE", eta_minutes=None)

        self._run(work, "Marked en route.")

    def _resolve_sos(self, alert: Dict[str, Any]) -> None:
        self._run(
            lambda: self.master_app.client.resolve_sos(alert["id"], "RESOLVED_SAFE"),
            "SOS resolved.",
        )

    def _show_timeline(self, incident_id: str) -> None:
        def work():
            try:
                events = self.master_app.client.incident_timeline(incident_id)
                sla = self.master_app.client.incident_sla(incident_id)
            except ApiError as exc:
                self.after(0, lambda: messagebox.showerror("CargoResQ", str(exc)))
                return
            self.after(0, lambda: self.master_app.open_timeline(incident_id, events, sla))

        threading.Thread(target=work, daemon=True).start()

    def _show_escrow(self, offer: Dict[str, Any]) -> None:
        escrow_id = offer.get("escrowId")
        if not escrow_id:
            return

        def work():
            try:
                ledger = self.master_app.client.escrow_ledger(escrow_id)
                escrow = self.master_app.client.escrow(escrow_id)
            except ApiError as exc:
                self.after(0, lambda: messagebox.showerror("CargoResQ", str(exc)))
                return
            self.after(0, lambda: self.master_app.open_escrow(escrow, ledger))

        threading.Thread(target=work, daemon=True).start()

    def _show_cold_chain(self, shipment: Dict[str, Any]) -> None:
        def work():
            try:
                data = self.master_app.client.cold_chain(shipment["id"])
            except ApiError as exc:
                self.after(0, lambda: messagebox.showerror("CargoResQ", str(exc)))
                return
            self.after(0, lambda: self.master_app.open_cold_chain(shipment, data))

        threading.Thread(target=work, daemon=True).start()

    def _open_report_breakdown(self) -> None:
        self.master_app.open_breakdown_dialog(self.shipments, self.fleet)

    def _open_add_truck(self) -> None:
        self.master_app.open_add_truck_dialog()

    def _open_add_shipment(self) -> None:
        self.master_app.open_add_shipment_dialog(self.fleet)

    # -- live feed -------------------------------------------------------
    def start_live_feed(self) -> None:
        client = self.master_app.client
        if not client.token:
            return
        self.feed = LiveFeed(client.websocket_url, client.token)
        self.feed.start()

    def _poll_events(self) -> None:
        if self.feed is not None:
            events = self.feed.drain()
            if events:
                self._handle_events(events)
            self.feed_status.configure(
                text="● live" if self.feed.connected else "● reconnecting",
                text_color=COLORS["sidebar_eyebrow"]
                if self.feed.connected
                else COLORS["amber"],
            )
        self.after(EVENT_POLL_MS, self._poll_events)

    def _handle_events(self, events: List[Dict[str, Any]]) -> None:
        """React to server-pushed events.

        Anything that changes what is on screen triggers a refresh rather than
        a partial in-place update -- the server is the source of truth, and a
        console that patches its own state drifts from it.
        """
        # Events that genuinely change what is on screen beyond a marker, and
        # so justify re-reading from the server.
        #
        # telemetry.location is deliberately NOT in this set. A moving truck
        # emits one every few seconds, and refetching seven endpoints on each
        # of them made the console crawl while the map it was redrawing had
        # already been updated in place below.
        interesting = {
            "breakdown.detected",
            "offer.created",
            "offer.bound",
            "offer.carrier_accepted",
            "offer.owner_confirmed",
            "escrow.state_changed",
            "telemetry.alert",
            "sos.raised",
            "sos.acknowledged",
            "sos.status_changed",
        }
        should_refresh = False
        for event in events:
            kind = event.get("type", "")
            if kind.startswith("incident.") or kind in interesting:
                should_refresh = True
            if kind == "telemetry.location":
                payload = event.get("payload", {})
                truck_id = payload.get("truckId")
                lat = payload.get("latitude")
                lng = payload.get("longitude")
                if truck_id and lat is not None and lng is not None:
                    known = False
                    for t in self.fleet:
                        if (t.get("truckId") or t.get("id")) == truck_id:
                            t["latitude"] = float(lat)
                            t["longitude"] = float(lng)
                            t["positionIsLive"] = True
                            t["speedKph"] = payload.get("speedKph")
                            t["headingDeg"] = payload.get("headingDeg")
                            t["lastSeenAt"] = payload.get("recordedAt")
                            t["positionSuspect"] = payload.get("positionSuspect", False)
                            t["suspectReason"] = payload.get("suspectReason")
                            known = True
                            break
                    if not known:
                        # First time we have heard from this truck: add it
                        # rather than dropping the fix on the floor until the
                        # next poll happens to pick it up.
                        self.fleet.append(
                            {
                                "truckId": truck_id,
                                "registrationNumber": payload.get(
                                    "registrationNumber", truck_id
                                ),
                                "status": "in_transit",
                                "latitude": float(lat),
                                "longitude": float(lng),
                                "positionIsLive": True,
                                "lastSeenAt": payload.get("recordedAt"),
                                "speedKph": payload.get("speedKph"),
                                "headingDeg": payload.get("headingDeg"),
                                "positionSuspect": payload.get("positionSuspect", False),
                                "suspectReason": payload.get("suspectReason"),
                            }
                        )
                    self._draw_map()
                    self._render_stats()
            if kind == "sos.raised":
                payload = event.get("payload", {})
                cat = str(payload.get("category", "EMERGENCY")).replace("_", " ")
                sev = payload.get("severity", "CRITICAL")

                if payload.get("isOwnDriver"):
                    # One of ours. Take over the screen: this is the case the
                    # console exists for.
                    self.master_app.set_status(f"SOS from our driver: {cat} ({sev})")
                    self.master_app.open_sos_emergency_dialog(payload)
                else:
                    # Somebody else's driver, broadcast to us because we have a
                    # truck nearby. Worth telling the operator about, but not
                    # worth seizing their screen -- they may be dealing with
                    # their own emergency.
                    self.master_app.set_status(
                        f"Nearby carrier SOS: {cat} ({sev}). See the SOS tab."
                    )
        if should_refresh:
            self.refresh()

    def stop(self) -> None:
        if self.feed is not None:
            self.feed.stop()
            self.feed = None


class CargoResQApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CargoResQ Operations Console")
        self.geometry("1440x900")
        self.minsize(1180, 740)
        self.configure(fg_color=COLORS["canvas"])

        self.client = CargoResQClient(API_ENDPOINT)
        self.dashboard: Optional[DashboardView] = None

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.status_bar = ctk.CTkLabel(
            self, text="", text_color=COLORS["muted"], anchor="w",
            font=ctk.CTkFont(size=11),
        )
        self.status_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 6))

        self.container: Optional[ctk.CTkFrame] = None
        self.show_login()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _swap(self, view: ctk.CTkFrame) -> None:
        if self.container is not None:
            if isinstance(self.container, DashboardView):
                self.container.stop()
            self.container.destroy()
        self.container = view
        view.grid(row=0, column=0, sticky="nsew")

    def show_login(self) -> None:
        self.dashboard = None
        self._swap(LoginView(self))

    def show_dashboard(self) -> None:
        self.dashboard = DashboardView(self)
        self._swap(self.dashboard)
        self.dashboard.company_label.configure(
            text=(self.client.company_name or "").upper()
        )
        self.set_status(f"Signed in as {self.client.company_name}")

    def connect(self, endpoint: str, email: str, password: str) -> None:
        self.client = CargoResQClient(endpoint or API_ENDPOINT)
        self.client.login(email, password)

    def sign_out(self) -> None:
        self.client.logout()
        self.show_login()

    def set_status(self, message: str) -> None:
        self.status_bar.configure(text=message)

    def _on_close(self) -> None:
        if isinstance(self.container, DashboardView):
            self.container.stop()
        self.destroy()

    # -- dialogs ---------------------------------------------------------
    def _dialog(self, title: str, width: int = 620, height: int = 560) -> ctk.CTkToplevel:
        window = ctk.CTkToplevel(self)
        window.title(title)
        window.geometry(f"{width}x{height}")
        window.configure(fg_color=COLORS["canvas"])
        window.transient(self)
        # Grab must wait for the window to be mapped, or it fails on Windows.
        window.after(120, lambda: window.grab_set() if window.winfo_exists() else None)
        return window

    def open_timeline(self, incident_id: str, events: List[Dict], sla: Dict) -> None:
        window = self._dialog(f"Audit trail - {incident_id}")
        heading(window, "Audit trail", size=18).pack(anchor="w", padx=24, pady=(22, 2))
        body(
            window,
            "Append-only. Every state change, who made it and when.",
            size=11,
        ).pack(anchor="w", padx=24, pady=(0, 14))

        rows = [
            (key.title(), f"{value.get('status')} ({value.get('actualSec') or '--'}s)")
            for key, value in sla.items()
        ]
        KeyValueGrid(window, rows).pack(fill="x", padx=24, pady=(0, 14))

        listing = scrollable(window)
        listing.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        for event in events:
            ListRow(
                listing,
                state_label(event.get("newState") or event.get("type", "")),
                f"{event.get('actorId', 'system')}  |  {relative_time(event.get('createdAt'))}",
            ).pack(fill="x", pady=3, padx=2)

    def open_escrow(self, escrow: Dict, ledger: Dict) -> None:
        window = self._dialog("Escrow", height=600)
        heading(window, "Escrow", size=18).pack(anchor="w", padx=24, pady=(22, 2))
        body(
            window,
            "Funds are held from the moment both parties commit, and released "
            "only once the recorded condition log confirms in-spec delivery.",
            size=11,
        ).pack(anchor="w", padx=24, pady=(0, 14))

        KeyValueGrid(
            window,
            [
                ("State", state_label(escrow.get("state", ""))),
                ("Amount", money(escrow.get("amountInr"))),
                ("Carrier payout", money(escrow.get("carrierPayoutInr"))),
                ("Balanced", "yes" if ledger.get("balance", {}).get("balanced") else "NO"),
            ],
        ).pack(fill="x", padx=24, pady=(0, 14))

        listing = scrollable(window)
        listing.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        for entry in ledger.get("entries", []):
            amount = entry.get("debitInr") or entry.get("creditInr")
            side = "debit" if entry.get("debitInr") else "credit"
            ListRow(
                listing,
                f"{entry.get('account')}  {side} {money(amount)}",
                f"{entry.get('postingType')}  |  {entry.get('memo') or ''}",
            ).pack(fill="x", pady=3, padx=2)

    def open_cold_chain(self, shipment: Dict, data: Dict) -> None:
        window = self._dialog("Cold chain", height=580)
        heading(window, shipment.get("cargo_type", "Shipment"), size=18).pack(
            anchor="w", padx=24, pady=(22, 2)
        )
        body(window, "Condition readings recorded by the platform.", size=11).pack(
            anchor="w", padx=24, pady=(0, 14)
        )

        KeyValueGrid(
            window,
            [
                ("Required max", f"{data.get('requiredMaxTempC')} C"),
                ("Readings", str(data.get("readingCount", 0))),
                ("Range", f"{data.get('minTempC')} to {data.get('maxTempC')} C"),
                ("Breaches", str(data.get("breachCount", 0))),
            ],
        ).pack(fill="x", padx=24, pady=(0, 14))

        listing = scrollable(window)
        listing.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        if not data.get("readings"):
            EmptyState(
                listing,
                "No readings recorded",
                "Escrow cannot be settled automatically without condition data. "
                "It will report insufficient evidence rather than release funds.",
            ).pack(fill="x")
        for reading in data.get("readings", [])[-40:]:
            ListRow(
                listing,
                f"{reading.get('temperatureC')} C",
                f"{relative_time(reading.get('at'))}  |  {reading.get('trustLevel')}",
            ).pack(fill="x", pady=3, padx=2)

    def open_breakdown_dialog(self, shipments: List[Dict], fleet: List[Dict]) -> None:
        window = self._dialog("Report a breakdown", height=520)
        heading(window, "Report a breakdown", size=18).pack(anchor="w", padx=24, pady=(22, 2))
        body(
            window,
            "Normally raised by the driver from the app. Use this when a "
            "driver has reported by phone.",
            size=11,
        ).pack(anchor="w", padx=24, pady=(0, 16))

        in_transit = [s for s in shipments if s.get("status") == "in_transit"]
        if not in_transit:
            EmptyState(window, "No shipments in transit").pack(fill="x", padx=24)
            return

        labels = [f"{s['cargo_type']} ({s['id']})" for s in in_transit]
        choice = ctk.CTkOptionMenu(window, values=labels, width=520, height=36,
                                   fg_color=COLORS["card"], button_color=COLORS["teal"],
                                   text_color=COLORS["ink"])
        choice.pack(padx=24, pady=(0, 12))

        lat_entry = self._labelled_entry(window, "Latitude", "18.5204")
        lng_entry = self._labelled_entry(window, "Longitude", "73.8567")
        hours_entry = self._labelled_entry(window, "Hours until cargo is at risk", "2")

        status = body(window, "", size=11, color=COLORS["red"])
        status.pack(anchor="w", padx=24)

        def submit():
            index = labels.index(choice.get())
            shipment = in_transit[index]
            try:
                payload = {
                    "shipment_id": shipment["id"],
                    "lat": float(lat_entry.get()),
                    "lng": float(lng_entry.get()),
                    "hours_to_spoilage": float(hours_entry.get() or 0) or None,
                }
            except ValueError:
                status.configure(text="Latitude, longitude and hours must be numbers.")
                return

            def work():
                self.client.report_breakdown(payload)

            try:
                work()
            except ApiError as exc:
                status.configure(text=str(exc))
                return
            window.destroy()
            if self.dashboard:
                self.dashboard.refresh()

        primary_button(window, "Report breakdown", submit, width=520).pack(
            padx=24, pady=18
        )

    def open_add_truck_dialog(self) -> None:
        window = self._dialog("Add a truck", height=640)
        heading(window, "Add a truck", size=18).pack(anchor="w", padx=24, pady=(22, 14))

        reg = self._labelled_entry(window, "Registration number", "")
        lat = self._labelled_entry(window, "Latitude", "18.5204")
        lng = self._labelled_entry(window, "Longitude", "73.8567")
        volume = self._labelled_entry(window, "Max volume (m3)", "18")
        weight = self._labelled_entry(window, "Max payload (kg)", "5000")
        min_temp = self._labelled_entry(window, "Minimum temperature (C, blank if not reefer)", "")

        refrigerated = ctk.CTkCheckBox(window, text="Refrigerated", text_color=COLORS["ink"])
        refrigerated.pack(anchor="w", padx=24, pady=(4, 2))
        hazmat = ctk.CTkCheckBox(window, text="Hazmat certified", text_color=COLORS["ink"])
        hazmat.pack(anchor="w", padx=24, pady=(0, 8))

        status = body(window, "", size=11, color=COLORS["red"])
        status.pack(anchor="w", padx=24)

        def submit():
            try:
                payload = {
                    "registration_number": reg.get().strip(),
                    "latitude": float(lat.get()),
                    "longitude": float(lng.get()),
                    "max_volume_m3": float(volume.get()),
                    "max_weight_kg": float(weight.get()),
                    "refrigerated": bool(refrigerated.get()),
                    "hazmat_certified": bool(hazmat.get()),
                    "min_temp_c": float(min_temp.get()) if min_temp.get().strip() else None,
                }
            except ValueError:
                status.configure(text="Check the numeric fields.")
                return
            try:
                self.client.create_truck(payload)
            except ApiError as exc:
                status.configure(text=str(exc))
                return
            window.destroy()
            if self.dashboard:
                self.dashboard.refresh()

        primary_button(window, "Add truck", submit, width=520).pack(padx=24, pady=16)

    def open_add_shipment_dialog(self, fleet: List[Dict]) -> None:
        window = self._dialog("Add a shipment", height=660)
        heading(window, "Add a shipment", size=18).pack(anchor="w", padx=24, pady=(22, 14))

        if not fleet:
            EmptyState(window, "Register a truck first").pack(fill="x", padx=24)
            return

        labels = [f"{t.get('registrationNumber')} ({t['truckId']})" for t in fleet]
        truck_choice = ctk.CTkOptionMenu(window, values=labels, width=520, height=36,
                                         fg_color=COLORS["card"], button_color=COLORS["teal"],
                                         text_color=COLORS["ink"])
        truck_choice.pack(padx=24, pady=(0, 12))

        cargo = self._labelled_entry(window, "Cargo description", "Refrigerated insulin")
        value = self._labelled_entry(window, "Declared value (INR)", "480000")
        weight = self._labelled_entry(window, "Weight (kg)", "850")
        volume = self._labelled_entry(window, "Volume (m3)", "3.5")
        max_temp = self._labelled_entry(window, "Maximum temperature (C, blank if none)", "8")

        refrigerated = ctk.CTkCheckBox(window, text="Requires refrigeration", text_color=COLORS["ink"])
        refrigerated.select()
        refrigerated.pack(anchor="w", padx=24, pady=(4, 2))
        hazmat = ctk.CTkCheckBox(window, text="Hazmat", text_color=COLORS["ink"])
        hazmat.pack(anchor="w", padx=24, pady=(0, 8))

        status = body(window, "", size=11, color=COLORS["red"])
        status.pack(anchor="w", padx=24)

        def submit():
            try:
                payload = {
                    "truck_id": fleet[labels.index(truck_choice.get())]["truckId"],
                    "cargo_type": cargo.get().strip(),
                    "value_inr": float(value.get()),
                    "weight_kg": float(weight.get()),
                    "volume_m3": float(volume.get()),
                    "requires_refrigeration": bool(refrigerated.get()),
                    "is_hazmat": bool(hazmat.get()),
                    "required_max_temp_c": (
                        float(max_temp.get()) if max_temp.get().strip() else None
                    ),
                }
            except ValueError:
                status.configure(text="Check the numeric fields.")
                return
            try:
                self.client.create_shipment(payload)
            except ApiError as exc:
                status.configure(text=str(exc))
                return
            window.destroy()
            if self.dashboard:
                self.dashboard.refresh()

        primary_button(window, "Add shipment", submit, width=520).pack(padx=24, pady=16)

    def _labelled_entry(self, master: Any, label: str, initial: str) -> ctk.CTkEntry:
        ctk.CTkLabel(
            master, text=label, anchor="w", text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=24, pady=(6, 3))
        entry = ctk.CTkEntry(
            master, width=520, height=36, border_color=COLORS["line"], fg_color="#FAFCFB"
        )
        entry.pack(padx=24)
        if initial:
            entry.insert(0, initial)
        return entry

    def open_sos_emergency_dialog(self, payload: Dict[str, Any]) -> None:
        """The emergency response screen.

        Two questions matter here, and the screen answers both from the
        server rather than from plausible-looking constants:

          * who has already been told and how far out they are, and
          * which compatible trucks could be commissioned right now.

        The second is the one that lets a dispatcher reroute instantly, so it
        is wired to the real offer handshake: escalate the SOS to an incident,
        offer it to the ranked carriers, and bind whichever the owner confirms.
        """
        try:
            self.bell()
        except Exception:
            pass

        sos_id = str(payload.get("sosId") or payload.get("alertId") or payload.get("id") or "")
        category = str(payload.get("category") or "EMERGENCY").replace("_", " ").upper()
        severity = str(payload.get("severity") or "CRITICAL").upper()

        # The live event carries very little; the full record has the rest.
        alert: Dict[str, Any] = dict(payload)
        if sos_id and self.dashboard:
            for known in self.dashboard.own_sos:
                if known.get("id") == sos_id:
                    alert = {**known, **{k: v for k, v in payload.items() if v is not None}}
                    break

        lat = _as_float(alert.get("latitude") or alert.get("approxLat") or alert.get("lat"))
        lng = _as_float(alert.get("longitude") or alert.get("approxLng") or alert.get("lng"))

        window = self._dialog(f"Emergency: {category}", width=860, height=760)

        # -- banner ------------------------------------------------------
        banner = ctk.CTkFrame(window, fg_color=COLORS["red_soft"], corner_radius=12)
        banner.pack(fill="x", padx=20, pady=(18, 10))

        top_row = ctk.CTkFrame(banner, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(14, 4))
        heading(top_row, f"{category} emergency", size=19).pack(side="left")
        fg, bg = severity_color(severity)
        badge(top_row, severity, fg, bg).pack(side="right")

        condition = alert.get("condition") or {}
        situation = (
            alert.get("landmarkNote")
            or condition.get("note")
            or "No situation detail reported"
        )
        body(banner, situation, size=12, color=COLORS["ink"]).pack(
            anchor="w", padx=16, pady=(0, 4)
        )

        location_line = (
            f"{lat:.5f}, {lng:.5f}" if lat is not None and lng is not None
            else "No GPS fix reported"
        )
        driver_line = alert.get("driverId") or "driver"
        body(
            banner,
            f"Truck {alert.get('truckId') or '--'}  |  {driver_line}  |  {location_line}",
            size=11,
        ).pack(anchor="w", padx=16, pady=(0, 6))

        # Repeated here because this is the screen someone stares at during an
        # emergency, and it is the moment they might assume otherwise.
        body(
            banner,
            "CargoResQ has alerted your company and nearby carriers. It has NOT "
            "called police, ambulance or fire. Dial 112 for those.",
            size=10,
        ).pack(anchor="w", padx=16, pady=(0, 12))

        # -- actions -----------------------------------------------------
        action_bar = ctk.CTkFrame(window, fg_color="transparent")
        action_bar.pack(fill="x", padx=20, pady=(0, 10))
        status_label = body(action_bar, "", size=11, color=COLORS["teal"])

        def show(message: str, ok: bool = True) -> None:
            status_label.configure(
                text=message, text_color=COLORS["teal"] if ok else COLORS["red"]
            )

        def centre_on_map() -> None:
            if not self.dashboard:
                return
            self.dashboard.switch_tab("Command centre")
            focus_lat = state.get("lat")
            focus_lng = state.get("lng")
            if focus_lat is not None and focus_lng is not None:
                self.dashboard.cc_map_panel.focus_on(focus_lat, focus_lng, zoom=14)

        secondary_button(action_bar, "Show on map", centre_on_map, width=110).pack(
            side="left", padx=(0, 8)
        )

        def mark_resolved() -> None:
            if not sos_id:
                return

            def work() -> None:
                try:
                    self.client.resolve_sos(sos_id, "RESOLVED_SAFE")
                    window.after(0, lambda: show("Marked resolved."))
                    if self.dashboard:
                        window.after(0, self.dashboard.refresh)
                except ApiError as exc:
                    window.after(0, lambda: show(str(exc), ok=False))

            threading.Thread(target=work, daemon=True).start()

        secondary_button(action_bar, "Mark resolved", mark_resolved, width=120).pack(
            side="left", padx=(0, 8)
        )
        status_label.pack(side="left", padx=8)

        # -- who is already coming ---------------------------------------
        responders_card = card(window)
        responders_card.pack(fill="x", padx=20, pady=(0, 10))
        heading(responders_card, "Carriers notified", size=15).pack(
            anchor="w", padx=16, pady=(12, 2)
        )
        responders_holder = ctk.CTkFrame(responders_card, fg_color="transparent")
        responders_holder.pack(fill="x", padx=4, pady=(0, 12))

        def render_responders(responders: List[Dict[str, Any]]) -> None:
            for child in responders_holder.winfo_children():
                child.destroy()
            if not responders:
                body(
                    responders_holder,
                    "Nobody has been notified yet. Commission a rescue below.",
                    size=11,
                    color=COLORS["amber"],
                ).pack(anchor="w", padx=12)
                return
            for responder in responders:
                line = ctk.CTkFrame(responders_holder, fg_color="transparent")
                line.pack(fill="x", padx=12, pady=2)
                eta = responder.get("etaMinutes")
                eta_text = (
                    "no ETA" if eta is None
                    else (f"~{eta:.0f} min" if responder.get("etaIsEstimate")
                          else f"{eta:.0f} min (stated)")
                )
                distance = responder.get("distanceKm")
                body(
                    line,
                    f"{responder.get('companyName', 'Carrier')}  ·  "
                    f"{distance:.1f} km  ·  {eta_text}" if distance is not None
                    else f"{responder.get('companyName', 'Carrier')}  ·  {eta_text}",
                    size=11,
                    color=COLORS["ink"],
                ).pack(side="left")
                state = responder.get("status", "NOTIFIED")
                colour = COLORS["teal"] if state in {"EN_ROUTE", "ON_SCENE"} else COLORS["muted"]
                body(line, state.replace("_", " ").title(), size=10, color=colour).pack(
                    side="right"
                )

        render_responders(alert.get("responders") or [])

        # -- compatible trucks, and commissioning one --------------------
        candidates_card = card(window)
        candidates_card.pack(fill="both", expand=True, padx=20, pady=(0, 18))

        header = ctk.CTkFrame(candidates_card, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 4))
        heading(header, "Compatible trucks nearby", size=15).pack(anchor="w")
        subtitle = body(header, "Searching the network...", size=11)
        subtitle.pack(anchor="w")

        listing = scrollable(candidates_card)
        listing.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # The live broadcast carries a deliberately coarsened position, so the
        # search below starts approximate and is corrected the moment the full
        # record loads.
        state: Dict[str, Any] = {
            "incident_id": alert.get("incidentId"),
            "lat": lat,
            "lng": lng,
        }

        def render_offers(offers: List[Dict[str, Any]]) -> None:
            """Offers already made: the actual negotiation."""
            for child in listing.winfo_children():
                child.destroy()

            subtitle.configure(
                text=(
                    f"{len(offers)} carrier(s) have been offered this rescue. "
                    "Confirm one to bind it."
                )
            )
            for offer in offers:
                row = card(listing, fg_color=COLORS["row"])
                row.pack(fill="x", pady=4, padx=4)

                top = ctk.CTkFrame(row, fg_color="transparent")
                top.pack(fill="x", padx=12, pady=(10, 2))
                heading(top, money(offer.get("priceTotalInr")), size=14).pack(side="left")
                ofg, obg = state_color(offer.get("state", ""))
                badge(top, state_label(offer.get("state", "")), ofg, obg).pack(side="right")

                eta = offer.get("etaMinutes")
                distance = offer.get("distanceKm")
                body(
                    row,
                    f"Truck {offer.get('carrierTruckId')}  ·  "
                    + (f"{distance:.1f} km away  ·  " if distance is not None else "")
                    + (f"{eta:.0f} min out" if eta is not None else "ETA unknown")
                    + f"  ·  score {offer.get('rescueScore', 0):.0f}",
                    size=11,
                    color=COLORS["ink"],
                ).pack(anchor="w", padx=12)

                reputation = offer.get("carrierReputation") or {}
                if reputation:
                    if reputation.get("isNewCounterparty"):
                        rep_text = "New to the network, no completed rescues"
                        rep_colour = COLORS["amber"]
                    else:
                        rep_text = (
                            f"Trust {reputation.get('trustScore')}  ·  "
                            f"{reputation.get('rescuesCompleted')} rescues completed"
                        )
                        rep_colour = COLORS["teal"]
                    body(row, rep_text, size=10, color=rep_colour).pack(anchor="w", padx=12)

                for reason in (offer.get("scoreReasons") or [])[:2]:
                    body(row, f"  · {reason}", size=10).pack(anchor="w", padx=12)

                buttons = ctk.CTkFrame(row, fg_color="transparent")
                buttons.pack(fill="x", padx=12, pady=(8, 10))

                if offer.get("state") in {"PENDING", "CARRIER_ACCEPTED"}:
                    label = (
                        "Confirm this rescue" if offer.get("carrierAccepted")
                        else "Pre-confirm"
                    )
                    primary_button(
                        buttons, label, lambda o=offer: confirm(o), height=30
                    ).pack(side="left", padx=(0, 8))
                elif offer.get("state") == "BOUND":
                    body(
                        buttons,
                        "Bound. The carrier is committed and the funds are held.",
                        size=11,
                        color=COLORS["teal"],
                    ).pack(side="left")

        def render_candidates(candidates: List[Dict[str, Any]]) -> None:
            """Trucks that could be offered the job, ranked."""
            for child in listing.winfo_children():
                child.destroy()

            if not candidates:
                subtitle.configure(text="No compatible truck is within range.")
                EmptyState(
                    listing,
                    "No compatible truck nearby",
                    "Nothing in range can carry this load. Widen the search or "
                    "arrange help outside the network.",
                ).pack(fill="x", pady=16)
                return

            subtitle.configure(
                text=f"{len(candidates)} compatible truck(s) found. "
                     "Commissioning offers them the job."
            )

            for candidate in candidates:
                row = card(listing, fg_color=COLORS["row"])
                row.pack(fill="x", pady=4, padx=4)

                top = ctk.CTkFrame(row, fg_color="transparent")
                top.pack(fill="x", padx=12, pady=(10, 2))
                heading(
                    top, candidate.get("registrationNumber") or candidate.get("truckId", "Truck"),
                    size=13,
                ).pack(side="left")
                badge(
                    top, f"score {candidate.get('score', 0):.0f}",
                    COLORS["teal"], COLORS["teal_soft"],
                ).pack(side="right")

                eta = candidate.get("etaMinutes")
                distance = candidate.get("distanceKm")
                price = (candidate.get("price") or {}).get("total_inr")
                bits = []
                if eta is not None:
                    bits.append(f"{eta:.0f} min out")
                if distance is not None:
                    bits.append(f"{distance:.1f} km")
                if candidate.get("trustScore") is not None:
                    bits.append(f"trust {candidate['trustScore']:.0f}")
                if price is not None:
                    bits.append(money(price))
                if not candidate.get("positionIsLive", True):
                    bits.append("position not confirmed recently")
                body(row, "  ·  ".join(bits) or "--", size=11, color=COLORS["ink"]).pack(
                    anchor="w", padx=12
                )

                for reason in (candidate.get("reasons") or [])[:2]:
                    body(row, f"  · {reason}", size=10).pack(anchor="w", padx=12)

                body(row, "", size=2).pack()

            commission_bar = ctk.CTkFrame(listing, fg_color="transparent")
            commission_bar.pack(fill="x", pady=(10, 4), padx=4)
            primary_button(
                commission_bar,
                "Commission a rescue from these carriers",
                commission,
                height=36,
            ).pack(fill="x")

        def confirm(offer: Dict[str, Any]) -> None:
            def work() -> None:
                try:
                    result = self.client.confirm_offer(offer["id"])
                    bound = result.get("bound")
                    window.after(
                        0,
                        lambda: show(
                            "Rescue bound. The carrier is on their way."
                            if bound
                            else "Confirmed. It binds once the carrier accepts."
                        ),
                    )
                    load(refresh_offers=True)
                    if self.dashboard:
                        window.after(0, self.dashboard.refresh)
                except ApiError as exc:
                    window.after(0, lambda: show(str(exc), ok=False))

            threading.Thread(target=work, daemon=True).start()

        def commission() -> None:
            """Escalate the SOS to an incident and offer it to the carriers."""
            if not sos_id:
                show("This alert has no id to escalate.", ok=False)
                return

            show("Commissioning...")

            def work() -> None:
                try:
                    incident_id = state.get("incident_id")
                    if not incident_id:
                        escalated = self.client.escalate_sos_to_incident(sos_id)
                        incident_id = escalated["incidentId"]
                        state["incident_id"] = incident_id

                    self.client.fan_out_offers(incident_id, radius_km=100.0, max_candidates=5)
                    window.after(0, lambda: show("Offers sent. Waiting on carriers."))
                    load(refresh_offers=True)
                    if self.dashboard:
                        window.after(0, self.dashboard.refresh)
                except ApiError as exc:
                    window.after(0, lambda: show(str(exc), ok=False))

            threading.Thread(target=work, daemon=True).start()

        def load(refresh_offers: bool = False) -> None:
            """Fetch whatever the current stage of the negotiation is."""

            def work() -> None:
                responders: List[Dict[str, Any]] = []
                offers: List[Dict[str, Any]] = []
                candidates: List[Dict[str, Any]] = []
                error: Optional[str] = None

                try:
                    if sos_id:
                        fresh = self.client.sos(sos_id)
                        responders = fresh.get("responders") or []
                        if fresh.get("incidentId"):
                            state["incident_id"] = fresh["incidentId"]
                        # Exact coordinates, replacing the coarsened ones the
                        # broadcast carried.
                        exact_lat = _as_float(fresh.get("latitude"))
                        exact_lng = _as_float(fresh.get("longitude"))
                        if exact_lat is not None and exact_lng is not None:
                            state["lat"] = exact_lat
                            state["lng"] = exact_lng
                except ApiError as exc:
                    error = str(exc)

                incident_id = state.get("incident_id")
                if incident_id:
                    try:
                        offers = self.client.incident_offers(incident_id)
                    except ApiError as exc:
                        error = error or str(exc)

                search_lat = state.get("lat")
                search_lng = state.get("lng")
                if not offers and search_lat is not None and search_lng is not None:
                    try:
                        result = self.client.candidates(
                            {"lat": search_lat, "lng": search_lng,
                             "radiusKm": 100.0, "limit": 6}
                        )
                        candidates = result.get("candidates", [])
                    except ApiError as exc:
                        error = error or str(exc)

                def apply() -> None:
                    if not window.winfo_exists():
                        return
                    render_responders(responders)
                    if offers:
                        render_offers(offers)
                    else:
                        render_candidates(candidates)
                    if error:
                        show(error, ok=False)

                window.after(0, apply)

            threading.Thread(target=work, daemon=True).start()

        load()


def main() -> None:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    CargoResQApp().mainloop()


if __name__ == "__main__":
    main()
