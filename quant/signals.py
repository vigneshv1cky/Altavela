"""Quantitative signals for prediction-market trading — no LLMs.

Computes statistical indicators from market data and ledger history.
All signals output a score from -100 (strong SELL / BUY_NO) to +100 (strong BUY_YES).
Positive = bullish, negative = bearish.
"""

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("altavela.quant")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Core signals
# ---------------------------------------------------------------------------

def mean_reversion(market_prices: list[float], window: int = 5) -> float:
    """How far is current YES price from its recent average?
    Positive = price below average (buy the dip), negative = above (sell the rip).
    Returns -100 to +100."""
    if len(market_prices) < 2 or not market_prices:
        return 0
    current = market_prices[0]
    avg = sum(market_prices[-window:]) / min(len(market_prices), window)
    if avg <= 0:
        return 0
    deviation = (avg - current) / avg * 100  # positive if current < avg
    return max(-100, min(100, deviation * 3))  # amplify, cap at ±100


def momentum(current_yes: float, prev_yes: float | None,
             age_hours: float = 0) -> float:
    """Price velocity with time decay. Positive = upward momentum.
    Returns -100 to +100."""
    if prev_yes is None or prev_yes <= 0 or age_hours <= 0:
        return 0
    change_pct = (current_yes - prev_yes) / prev_yes * 100
    # Decay older moves: 12h half-life
    weight = 2.0 ** (-age_hours / 12)
    signal = change_pct * weight * 4  # scale
    return max(-100, min(100, signal))


def volume_conviction(volume: float, liquidity: float) -> float:
    """How much conviction does volume show?
    High volume + high liquidity = efficient market (low edge).
    High volume + low liquidity = possible breakout.
    Returns -100 to +100 (positive = conviction in current direction)."""
    if volume <= 0:
        return 0
    if liquidity <= 0:
        liquidity = 1000
    ratio = volume / (liquidity + 1)
    if ratio > 10:    # very high volume relative to liquidity
        return 60
    elif ratio > 3:
        return 30
    elif ratio > 1:
        return 10
    return -20  # low volume = low conviction


def spread_signal(yes_price: float, no_price: float) -> float:
    """How tight is the spread? Tight = efficient, wide = opportunity.
    Returns positive if tight (liquid), negative if wide (illiquid)."""
    if yes_price <= 0 or no_price <= 0:
        return -50
    spread = abs(1.0 - yes_price - no_price)
    if spread < 0.02:    # tight spread
        return 20
    elif spread < 0.05:  # normal
        return 0
    elif spread < 0.10:  # wide
        return -20
    return -50           # very wide, avoid


def time_decay(end_date: str) -> float:
    """Urgency signal. Closer to resolution = more signal.
    Returns -100 to +100 (positive = urgency, good to trade now)."""
    if not end_date:
        return -30
    try:
        dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        hrs = (dt.replace(tzinfo=timezone.utc) - _now_utc()).total_seconds() / 3600
        if hrs < 0:
            return 0    # past, don't enter
        elif hrs < 1:
            return 80   # very urgent
        elif hrs < 6:
            return 50   # near term
        elif hrs < 24:
            return 20   # today
        elif hrs < 72:
            return 0    # this week
        return -20       # far future, low urgency
    except (ValueError, TypeError):
        return -30


def direction_bias(market_id: str, ledger_store) -> float:
    """Recent win/loss bias on this market. Positive = recent wins on BUY_YES.
    Returns -100 to +100."""
    try:
        with ledger_store._connect() as conn:
            rows = conn.execute(
                "SELECT direction, outcome FROM picks"
                " WHERE market_id=? AND arm IN ('TEAM','QUANT') AND resolved=1"
                " ORDER BY id DESC LIMIT 10",
                (market_id,),
            ).fetchall()
        if not rows:
            return 0
        score = 0
        decay = 1.0
        for r in rows:
            if r["outcome"] == 1.0:
                score += (30 if r["direction"] == "BUY_YES" else -30) * decay
            else:
                score += (-20 if r["direction"] == "BUY_YES" else 20) * decay
            decay *= 0.8
        return max(-100, min(100, score))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Composite scorer — combines all signals into one decision
# ---------------------------------------------------------------------------

def compute_composite(
    market_id: str,
    yes_price: float,
    no_price: float,
    volume: float,
    liquidity: float,
    end_date: str,
    prev_yes: float | None = None,
    prev_age_hours: float = 0,
    ledger_store=None,
    market_prices: list[float] | None = None,
) -> dict:
    """Combine all signals into a composite score and direction.
    Returns {direction, score, signals}."""

    signals = {}

    s_mr = mean_reversion(market_prices or [yes_price])
    signals["mean_reversion"] = round(s_mr, 1)

    s_mom = momentum(yes_price, prev_yes, prev_age_hours)
    signals["momentum"] = round(s_mom, 1)

    s_vol = volume_conviction(volume, liquidity)
    signals["volume"] = round(s_vol, 1)

    s_spread = spread_signal(yes_price, no_price)
    signals["spread"] = round(s_spread, 1)

    s_time = time_decay(end_date)
    signals["time"] = round(s_time, 1)

    if ledger_store:
        s_bias = direction_bias(market_id, ledger_store)
        signals["bias"] = round(s_bias, 1)
    else:
        s_bias = 0
        signals["bias"] = 0

    # Weights: momentum + mean_reversion dominate, others modulate
    composite = (
        s_mom * 0.25 +
        s_mr * 0.25 +
        s_vol * 0.15 +
        s_spread * 0.10 +
        s_time * 0.15 +
        s_bias * 0.10
    )

    direction = "BUY_YES" if composite > 0 else "BUY_NO"
    abs_score = min(100, abs(composite))

    return {
        "direction": direction,
        "score": round(abs_score, 1),
        "composite": round(composite, 1),
        "signals": signals,
    }
