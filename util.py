"""Shared utilities used across Altavela — price math, field access, filters."""

from typing import Any

DEFAULT_YES_PX = 0.5
DEFAULT_NO_PX = 0.5
DEFAULT_PRICES = (DEFAULT_YES_PX, DEFAULT_NO_PX)


# ---------------------------------------------------------------------------
# Price selection by direction
# ---------------------------------------------------------------------------

def entry_price(pick: dict, direction: str = "") -> float | None:
    """Return the entry price for a position given its direction."""
    d = direction or pick.get("direction", "")
    key = "market_yes_price" if d == "BUY_YES" else "market_no_price"
    return pick.get(key)


def current_price(prices: list | tuple, direction: str) -> float:
    """Return the relevant price (YES or NO) for a given direction."""
    idx = 0 if direction == "BUY_YES" else 1
    if isinstance(prices, (list, tuple)) and len(prices) > idx:
        return float(prices[idx])
    return DEFAULT_YES_PX


# ---------------------------------------------------------------------------
# P&L
# ---------------------------------------------------------------------------

def pnl_pct(exit_price: float, entry: float, decimals: int = 1) -> float | None:
    """Compute P&L as percentage return. Returns None if entry is invalid."""
    if not entry or entry <= 0:
        return None
    return round((exit_price - entry) / entry * 100, decimals)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_outcome(direction: str, resolved_price: float) -> float:
    """Return 1.0 (WIN) or 0.0 (LOSS) based on direction and resolved price."""
    if direction == "BUY_YES":
        return 1.0 if resolved_price > 0.5 else 0.0
    return 1.0 if resolved_price < 0.5 else 0.0


# ---------------------------------------------------------------------------
# Safe parse
# ---------------------------------------------------------------------------

def safe_float(val: Any, default: float = 0.0) -> float:
    """Parse a float, returning default on failure."""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_prices(raw: list, length: int = 2) -> list[float]:
    """Parse a list of price strings/numbers into rounded floats."""
    out = []
    for i in range(min(len(raw), length)):
        out.append(round(safe_float(raw[i], 0.0), 4))
    while len(out) < length:
        out.append(DEFAULT_YES_PX)
    return out


# ---------------------------------------------------------------------------
# Cooldown filter
# ---------------------------------------------------------------------------

def apply_cooldown_filter(
    markets: list[dict],
    recent: dict[str, dict],
    cooldown_hours: float,
    min_move_pct: float,
) -> tuple[list[dict], int]:
    """Filter out markets debated recently unless price moved > threshold.
    Returns (fresh_markets, skipped_count)."""
    fresh = []
    skipped = 0
    for m in markets:
        mid = m.get("id", "")
        if mid in recent:
            prev = recent[mid]
            prices = m.get("prices", [DEFAULT_YES_PX, DEFAULT_NO_PX])
            cur_yes = prices[0] if prices else DEFAULT_YES_PX
            prev_yes = prev.get("yes_price")
            if prev_yes is not None and prev_yes > 0:
                move = abs(cur_yes - prev_yes) / prev_yes * 100
                if move < min_move_pct:
                    skipped += 1
                    continue
        fresh.append(m)
    return fresh, skipped
