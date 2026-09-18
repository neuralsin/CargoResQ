"""
The visual system, in one place.

Restrained on purpose: white surfaces, one accent, a single 1px border, no
shadows. An operations console is read under pressure, so contrast carries the
meaning and colour is reserved for state.

These are the canonical tokens. The Android app mirrors them, so anything
changed here should be changed there too.
"""

COLORS = {
    "ink": "#101615",       # primary text, dark surfaces
    "muted": "#6F7B78",     # secondary text
    "line": "#E4EAE7",      # borders
    "canvas": "#F5F7F6",    # page background
    "card": "#FFFFFF",      # raised surfaces
    "row": "#FBFCFB",       # list rows
    "teal": "#0F766E",      # accent / positive
    "teal_dark": "#0A5C56",  # accent hover
    "teal_soft": "#E5F3F0",
    "amber": "#B7791F",     # in-progress / attention
    "amber_soft": "#FFF4D8",
    "red": "#B42318",       # failure / emergency
    "red_soft": "#FDECEA",
    "sidebar_active": "#21423F",
    "sidebar_text": "#B5C8C4",
    "sidebar_text_active": "#EAF5F2",
    "sidebar_eyebrow": "#8CCBC3",
    "sidebar_dim": "#7F9793",
}

RADIUS_CARD = 12
RADIUS_ROW = 10
RADIUS_PILL = 8
SIDEBAR_WIDTH = 230

#: Incident and escrow states, grouped by what an operator needs to do.
_TERMINAL_GOOD = {"ESCROW_RELEASED", "DELIVERED", "RELEASED", "RESOLVED", "CLOSED"}
_TERMINAL_BAD = {"CANCELLED", "DISPUTED", "EXPIRED", "WITHDRAWN", "SUPERSEDED", "DECLINED"}
_IN_FLIGHT = {
    "BREAKDOWN_REPORTED",
    "TRIAGING",
    "MATCHING",
    "RESCUE_OFFERED",
    "PENDING",
    "CARRIER_ACCEPTED",
    "OWNER_CONFIRMED",
    "BROADCASTING",
    "UNANSWERED",
    "PENDING_VERIFICATION",
}


def state_color(state: str) -> tuple[str, str]:
    """Return (foreground, background) for a state badge."""
    state = (state or "").upper()
    if state in _TERMINAL_GOOD:
        return COLORS["teal"], COLORS["teal_soft"]
    if state in _TERMINAL_BAD:
        return COLORS["red"], COLORS["red_soft"]
    if state in _IN_FLIGHT:
        return COLORS["amber"], COLORS["amber_soft"]
    return COLORS["muted"], COLORS["line"]


def severity_color(severity: str) -> tuple[str, str]:
    severity = (severity or "").upper()
    if severity == "CRITICAL":
        return COLORS["red"], COLORS["red_soft"]
    if severity == "WARN":
        return COLORS["amber"], COLORS["amber_soft"]
    return COLORS["muted"], COLORS["line"]


def state_label(state: str) -> str:
    """ESCROW_RELEASED -> Escrow released."""
    return (state or "unknown").replace("_", " ").capitalize()


def money(amount: float | None, currency: str = "INR") -> str:
    """Format an amount in the Indian numbering system.

    Lakhs and crores, because that is how the numbers will actually be read
    aloud by the people using this.
    """
    if amount is None:
        return "--"
    amount = float(amount)
    if amount >= 10_000_000:
        return f"{currency} {amount / 10_000_000:.2f} Cr"
    if amount >= 100_000:
        return f"{currency} {amount / 100_000:.2f} L"
    return f"{currency} {amount:,.0f}"


def minutes_label(minutes: float | None) -> str:
    if minutes is None:
        return "--"
    minutes = float(minutes)
    if minutes < 60:
        return f"{minutes:.0f} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours:.0f} h {rest:.0f} min"


def relative_time(iso: str | None) -> str:
    """'4 min ago'. An absolute timestamp makes staleness hard to judge."""
    if not iso:
        return "never"
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:19]
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = (datetime.now(timezone.utc) - when).total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 86400:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} d ago"
