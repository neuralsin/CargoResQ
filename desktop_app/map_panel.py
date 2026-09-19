"""
A real map.

The previous "Live Rescue Map" drew a grid on a canvas and placed markers at
`x = 48 + (i * 71) % width` -- the count of markers was real, their positions
were arithmetic on the list index, and the caption underneath read "live
coordinates from API". This draws OpenStreetMap tiles and puts every marker at
the coordinate the API actually returned.

Tiles come from OSM's public servers, so the widget is constructed with a
project-specific user agent as their tile usage policy requires, and tile
requests are cached on disk between runs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import customtkinter as ctk
from tkintermapview import TkinterMapView

from .theme import COLORS

#: OSM's standard tile layer. Changing this to a commercial provider is a
#: one-line edit plus that provider's API key.
TILE_SERVER = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"

#: Tiles are cached here so a demo does not re-download the same squares and
#: so the map still draws something when the network is slow.
TILE_CACHE = Path.home() / ".cargoresq" / "map_tiles.db"

MARKER_COLORS = {
    "incident": (COLORS["red"], "#FFFFFF"),
    "own_truck": (COLORS["teal"], "#FFFFFF"),
    "candidate": (COLORS["amber"], "#FFFFFF"),
    "rescuer": ("#0D9488", "#FFFFFF"),
    "sos": (COLORS["red"], "#FFFFFF"),
    "destination": (COLORS["ink"], "#FFFFFF"),
    # Safe storage. Deliberately not red or teal: a depot is neither an
    # emergency nor a truck, and on a crowded map the distinction has to be
    # readable at a glance.
    "storage": ("#4338CA", "#FFFFFF"),
    "storage_chosen": ("#1E3A8A", "#FFFFFF"),
    # A position the server flagged as spoofed or impossible. Drawn, but
    # visibly different from one that is trusted.
    "suspect": (COLORS["amber"], "#FFFFFF"),
}


class MapPanel(ctk.CTkFrame):
    """An OSM map with typed markers and route paths."""

    def __init__(
        self,
        master: Any,
        *,
        height: int = 420,
        initial_position: Tuple[float, float] = (20.5937, 78.9629),  # India
        initial_zoom: int = 5,
        on_marker_click: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(
            master,
            fg_color=COLORS["card"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["line"],
        )
        self._on_marker_click = on_marker_click
        self._markers: Dict[str, Any] = {}
        self._paths: List[Any] = []
        self._last_positions: Optional[Tuple[Tuple[float, float], ...]] = None

        # Whether the viewport still belongs to the code or to the person.
        #
        # The map used to re-frame itself every time any marker moved. With a
        # phone sending live GPS that is every few seconds, so zooming in was
        # pointless: the view snapped back before you could read it. Now the
        # first draw frames everything and after that the viewport is the
        # user's until they ask for it back.
        self._has_fitted = False
        self._user_controls_view = False

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)

        self.title = ctk.CTkLabel(
            header,
            text="Live map",
            text_color=COLORS["ink"],
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.title.grid(row=0, column=0, sticky="w")

        self.fit_button = ctk.CTkButton(
            header,
            text="Fit all",
            width=62,
            height=24,
            corner_radius=6,
            fg_color=COLORS["row"],
            hover_color=COLORS["line"],
            text_color=COLORS["ink"],
            border_width=1,
            border_color=COLORS["line"],
            font=ctk.CTkFont(size=10),
            command=self.refit,
        )
        self.fit_button.grid(row=0, column=1, sticky="e", padx=(0, 8))

        self.subtitle = ctk.CTkLabel(
            header,
            text="OpenStreetMap",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
        )
        self.subtitle.grid(row=0, column=2, sticky="e")

        TILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        self.map_view = TkinterMapView(
            self,
            height=height,
            corner_radius=0,
            # Passing this explicitly avoids the widget probing the parent for
            # a CustomTkinter colour, which differs across CTk major versions.
            bg_color=COLORS["card"],
            database_path=str(TILE_CACHE),
            use_database_only=False,
            max_zoom=19,
        )
        self.map_view.set_tile_server(TILE_SERVER, max_zoom=19)
        self.map_view.set_position(*initial_position)
        self.map_view.set_zoom(initial_zoom)
        self.map_view.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        # Any direct interaction means the person is looking at something
        # specific. Dragging the view out from under them at that point is
        # the single most irritating thing a live map can do.
        canvas = getattr(self.map_view, "canvas", self.map_view)
        for sequence in ("<Button-1>", "<B1-Motion>", "<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                canvas.bind(sequence, self._note_user_interaction, add="+")
            except Exception:
                pass

        self.footer = ctk.CTkLabel(
            self,
            text="No positions yet",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10),
        )
        self.footer.grid(row=2, column=0, sticky="w", padx=18, pady=(0, 10))

    # -- markers ---------------------------------------------------------
    def clear(self) -> None:
        for marker in self._markers.values():
            try:
                marker.delete()
            except Exception:
                pass
        self._markers.clear()
        for path in self._paths:
            try:
                path.delete()
            except Exception:
                pass
        self._paths.clear()

    def add_marker(
        self,
        key: str,
        lat: float,
        lng: float,
        text: str,
        kind: str = "own_truck",
        detail: str = "",
    ) -> None:
        """Place one marker at a genuine coordinate."""
        if lat is None or lng is None:
            return
        fill, text_color = MARKER_COLORS.get(kind, MARKER_COLORS["own_truck"])
        cb = self._on_marker_click
        cmd = (lambda _m, k=key, fn=cb: fn(k) if fn else None) if cb is not None else None
        try:
            marker = self.map_view.set_marker(
                float(lat),
                float(lng),
                text=text,
                marker_color_circle=text_color,
                marker_color_outside=fill,
                text_color=COLORS["ink"],
                command=cmd,
            )
        except Exception:
            return
        setattr(marker, "cargoresq_detail", detail)
        setattr(marker, "_spec", (lat, lng, text, kind))
        self._markers[key] = marker

    def sync_markers(self, specs: Dict[str, Dict[str, Any]]) -> None:
        """Incrementally sync markers without wiping and redrawing."""
        obsolete_keys = [k for k in self._markers if k not in specs]
        for k in obsolete_keys:
            try:
                self._markers[k].delete()
            except Exception:
                pass
            del self._markers[k]

        for key, spec in specs.items():
            lat = spec.get("lat")
            lng = spec.get("lng")
            if lat is None or lng is None:
                continue
            text = spec.get("text", "")
            kind = spec.get("kind", "own_truck")
            detail = spec.get("detail", "")

            if key in self._markers:
                marker = self._markers[key]
                cur_spec = getattr(marker, "_spec", None)
                if cur_spec == (lat, lng, text, kind):
                    continue
                try:
                    marker.set_position(float(lat), float(lng))
                    marker.set_text(text)
                    setattr(marker, "_spec", (lat, lng, text, kind))
                    setattr(marker, "cargoresq_detail", detail)
                    continue
                except Exception:
                    try:
                        marker.delete()
                    except Exception:
                        pass
                    del self._markers[key]

            fill, text_color = MARKER_COLORS.get(kind, MARKER_COLORS["own_truck"])
            cb = self._on_marker_click
            cmd = (lambda _m, k=key, fn=cb: fn(k) if fn else None) if cb is not None else None
            try:
                marker = self.map_view.set_marker(
                    float(lat),
                    float(lng),
                    text=text,
                    marker_color_circle=text_color,
                    marker_color_outside=fill,
                    text_color=COLORS["ink"],
                    command=cmd,
                )
                setattr(marker, "_spec", (lat, lng, text, kind))
                setattr(marker, "cargoresq_detail", detail)
                self._markers[key] = marker
            except Exception:
                pass

    def add_path(self, points: Iterable[Tuple[float, float]], color: str = COLORS["teal"]) -> None:
        pts = [(float(a), float(b)) for a, b in points if a is not None and b is not None]
        if len(pts) < 2:
            return
        try:
            path = self.map_view.set_path(pts, color=color, width=4)
        except Exception:
            return
        self._paths.append(path)

    # -- viewport --------------------------------------------------------
    def focus_on(self, lat: float, lng: float, zoom: int = 12) -> None:
        if lat is None or lng is None:
            return
        self.map_view.set_position(float(lat), float(lng))
        self.map_view.set_zoom(zoom)

    def fit_to_markers(self, positions: List[Tuple[float, float]]) -> None:
        """Frame every marker.

        With a single point there is nothing to fit, so it centres instead --
        fitting a degenerate box zooms to street level on one truck and loses
        all context.
        """
        points = [(float(a), float(b)) for a, b in positions if a is not None and b is not None]
        if not points:
            return
        if len(points) == 1:
            self.focus_on(points[0][0], points[0][1], zoom=12)
            return

        lats = [p[0] for p in points]
        lngs = [p[1] for p in points]
        pad = 0.02
        try:
            self.map_view.fit_bounding_box(
                (max(lats) + pad, min(lngs) - pad),
                (min(lats) - pad, max(lngs) + pad),
            )
        except Exception:
            self.focus_on(sum(lats) / len(lats), sum(lngs) / len(lngs), zoom=9)

    def _note_user_interaction(self, _event: Any = None) -> None:
        self._user_controls_view = True
        self.fit_button.configure(text="Fit all ↺")

    def refit(self) -> None:
        """Hand the viewport back to the map and re-frame everything."""
        self._user_controls_view = False
        self.fit_button.configure(text="Fit all")
        if self._last_positions:
            self.fit_to_markers(list(self._last_positions))

    def maybe_fit(self, positions: List[Tuple[float, float]]) -> None:
        """Frame the markers, but only while the viewport is still ours."""
        self._last_positions = tuple(positions)
        if self._user_controls_view or not positions:
            return
        if self._has_fitted:
            return
        self.fit_to_markers(positions)
        self._has_fitted = True

    def set_status(self, text: str) -> None:
        self.footer.configure(text=text)

    def set_title(self, title: str, subtitle: str = "") -> None:
        self.title.configure(text=title)
        if subtitle:
            self.subtitle.configure(text=subtitle)
