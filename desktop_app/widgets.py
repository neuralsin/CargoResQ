"""
Small reusable pieces of the console.

Kept separate so the view code reads as layout rather than as widget
construction.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

import customtkinter as ctk

from .theme import COLORS, RADIUS_CARD, RADIUS_PILL, RADIUS_ROW


def card(master: Any, **kwargs: Any) -> ctk.CTkFrame:
    """A white surface with a hairline border. The console's only container."""
    options = dict(
        fg_color=COLORS["card"],
        corner_radius=RADIUS_CARD,
        border_width=1,
        border_color=COLORS["line"],
    )
    options.update(kwargs)
    return ctk.CTkFrame(master, **options)


def eyebrow(master: Any, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master,
        text=text.upper(),
        text_color=COLORS["muted"],
        font=ctk.CTkFont(size=10, weight="bold"),
    )


def heading(master: Any, text: str, size: int = 16) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master,
        text=text,
        text_color=COLORS["ink"],
        font=ctk.CTkFont(size=size, weight="bold"),
    )


def body(master: Any, text: str, size: int = 12, color: Optional[str] = None) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master,
        text=text,
        text_color=color or COLORS["muted"],
        font=ctk.CTkFont(size=size),
        justify="left",
        anchor="w",
    )


def badge(master: Any, text: str, fg: str, bg: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master,
        text=text,
        text_color=fg,
        fg_color=bg,
        corner_radius=RADIUS_PILL,
        font=ctk.CTkFont(size=10, weight="bold"),
        padx=10,
        pady=4,
    )


def primary_button(master: Any, text: str, command: Callable, **kwargs: Any) -> ctk.CTkButton:
    options = dict(
        text=text,
        command=command,
        height=38,
        fg_color=COLORS["teal"],
        hover_color=COLORS["teal_dark"],
        text_color="#FFFFFF",
        font=ctk.CTkFont(size=13, weight="bold"),
        corner_radius=RADIUS_PILL,
    )
    options.update(kwargs)
    return ctk.CTkButton(master, **options)


def secondary_button(master: Any, text: str, command: Callable, **kwargs: Any) -> ctk.CTkButton:
    options = dict(
        text=text,
        command=command,
        height=36,
        fg_color=COLORS["card"],
        hover_color="#E9EFEC",
        text_color=COLORS["ink"],
        border_width=1,
        border_color=COLORS["line"],
        font=ctk.CTkFont(size=12),
        corner_radius=RADIUS_PILL,
    )
    options.update(kwargs)
    return ctk.CTkButton(master, **options)


def danger_button(master: Any, text: str, command: Callable, **kwargs: Any) -> ctk.CTkButton:
    options = dict(
        text=text,
        command=command,
        height=38,
        fg_color=COLORS["red"],
        hover_color="#8F1C13",
        text_color="#FFFFFF",
        font=ctk.CTkFont(size=13, weight="bold"),
        corner_radius=RADIUS_PILL,
    )
    options.update(kwargs)
    return ctk.CTkButton(master, **options)


class StatCard(ctk.CTkFrame):
    """One headline number with its label and a line of context."""

    def __init__(self, master: Any, title: str, value: str, detail: str,
                 accent: Optional[str] = None) -> None:
        super().__init__(
            master,
            fg_color=COLORS["card"],
            corner_radius=RADIUS_CARD,
            border_width=1,
            border_color=COLORS["line"],
        )
        eyebrow(self, title).pack(anchor="w", padx=16, pady=(14, 2))
        self.value_label = ctk.CTkLabel(
            self,
            text=value,
            text_color=accent or COLORS["ink"],
            font=ctk.CTkFont(size=26, weight="bold"),
        )
        self.value_label.pack(anchor="w", padx=16)
        ctk.CTkLabel(
            self,
            text=detail,
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=16, pady=(0, 14))


class ListRow(ctk.CTkFrame):
    """A clickable row in a scrollable list."""

    def __init__(
        self,
        master: Any,
        title: str,
        subtitle: str,
        badge_text: str = "",
        badge_colors: Tuple[str, str] = (COLORS["muted"], COLORS["line"]),
        on_click: Optional[Callable] = None,
        selected: bool = False,
    ) -> None:
        super().__init__(
            master,
            fg_color=COLORS["teal_soft"] if selected else COLORS["row"],
            corner_radius=RADIUS_ROW,
            border_width=1,
            border_color=COLORS["teal"] if selected else COLORS["line"],
        )
        left = ctk.CTkFrame(self, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True, padx=14, pady=11)

        ctk.CTkLabel(
            left,
            text=title,
            text_color=COLORS["ink"],
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            left,
            text=subtitle,
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=10),
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        if badge_text:
            badge(self, badge_text, badge_colors[0], badge_colors[1]).pack(
                side="right", padx=14, pady=13
            )

        if on_click:
            # Bind on every child too: clicking the label must select the row,
            # not fall through to nothing.
            for widget in (self, left, *left.winfo_children()):
                widget.bind("<Button-1>", lambda _e: on_click())
                widget.configure(cursor="hand2")


class EmptyState(ctk.CTkFrame):
    """What a list shows when there is genuinely nothing in it."""

    def __init__(self, master: Any, title: str, detail: str = "") -> None:
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(
            self,
            text=title,
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=13),
        ).pack(pady=(28, 4))
        if detail:
            ctk.CTkLabel(
                self,
                text=detail,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=11),
                wraplength=340,
                justify="center",
            ).pack(pady=(0, 24))


class KeyValueGrid(ctk.CTkFrame):
    """Label/value pairs, aligned."""

    def __init__(self, master: Any, rows: List[Tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.grid_columnconfigure(1, weight=1)
        for index, (label, value) in enumerate(rows):
            ctk.CTkLabel(
                self,
                text=label,
                text_color=COLORS["muted"],
                font=ctk.CTkFont(size=11),
                anchor="w",
            ).grid(row=index, column=0, sticky="w", pady=3, padx=(0, 14))
            ctk.CTkLabel(
                self,
                text=value,
                text_color=COLORS["ink"],
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w",
                wraplength=280,
                justify="left",
            ).grid(row=index, column=1, sticky="w", pady=3)


def scrollable(master: Any, **kwargs: Any) -> ctk.CTkScrollableFrame:
    options = dict(fg_color="transparent")
    options.update(kwargs)
    return ctk.CTkScrollableFrame(master, **options)
