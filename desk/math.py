"""Mathematical signals for prediction-market decision-making.

Computes quantitative indicators from market data and ledger history.
Passes structured signals to the researcher as evidence context.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from altavela.ledger import store

log = logging.getLogger("altavela.math")

# Time decay factor: how much to discount old price moves (hours)
_DECAY_HALF_LIFE = 12


def _time_decay(age_hours: float) -> float:
    """Exponential decay: weight = 0.5^(age/half_life)."""
    return 2.0 ** (-age_hours / _DECAY_HALF_LIFE)


def compute_signals(market_id: str, current_yes: float, current_no: float,
                    current_volume: float, market_end: str,
                    scout_direction: str) -> list[str]:
    """Compute mathematical signals for a prediction market.

    Returns list of evidence lines like:
      [MATH-VELOCITY] YES moved +0.05 in 2h (bullish)
      [MATH-UNCERTAINTY] price near 0.50 — high uncertainty
      [MATH-DECAY] 3 trades in last 24h, last 2 were wrong
    """
    signals: list[str] = []

    # 1. Price velocity — how fast is the market moving?
    prev = _last_price(market_id)
    if prev:
        prev_yes = prev.get("yes_price") or 0.5
        prev_ts = prev.get("ts", "")
        if prev_ts and prev_yes > 0:
            try:
                age_h = (_now_utc() - datetime.fromisoformat(prev_ts).replace(tzinfo=timezone.utc)).total_seconds() / 3600
                change = current_yes - prev_yes
                pct_change = abs(change) / prev_yes * 100
                direction_word = "bullish" if change > 0 else "bearish" if change < 0 else "flat"
                weight = _time_decay(age_h)
                if age_h > 0.1 and pct_change > 0.1:  # at least 0.1% move
                    signals.append(
                        f"[MATH-VELOCITY] YES moved {change:+.4f} in {age_h:.1f}h "
                        f"({pct_change:.1f}% {direction_word}, weight={weight:.2f})")
            except (ValueError, TypeError):
                pass

    # 2. Uncertainty — distance from 50/50
    dist = abs(current_yes - 0.5)
    if dist < 0.05:
        signals.append("[MATH-UNCERTAINTY] Price near 0.50 — maximum uncertainty, high variance")
    elif dist < 0.15:
        signals.append(f"[MATH-UNCERTAINTY] Price at {current_yes:.2f} — moderate uncertainty")
    else:
        signals.append(f"[MATH-UNCERTAINTY] Price at {current_yes:.2f} — consensus forming, less edge")

    # 3. Time to resolution
    if market_end:
        try:
            end = datetime.fromisoformat(market_end.replace("Z", "+00:00").replace("T", " "))
            hrs = (end.replace(tzinfo=timezone.utc) - _now_utc()).total_seconds() / 3600
            if hrs < 0:
                signals.append("[MATH-EXPIRY] Market past end date — may resolve soon")
            elif hrs < 1:
                signals.append(f"[MATH-EXPIRY] Resolves in {hrs:.1f} hours — urgency high")
            elif hrs < 24:
                signals.append(f"[MATH-EXPIRY] Resolves in {hrs:.0f} hours — near-term")
            else:
                signals.append(f"[MATH-EXPIRY] Resolves in {hrs / 24:.0f} days — long horizon")
        except (ValueError, TypeError):
            pass

    # 4. Volume significance
    if current_volume > 500000:
        signals.append("[MATH-VOLUME] High volume (>$500K) — deep liquidity, market efficient")
    elif current_volume > 50000:
        signals.append(f"[MATH-VOLUME] Volume ${current_volume/1000:.0f}K — decent liquidity")
    else:
        signals.append("[MATH-VOLUME] Low volume — thin market, prices less reliable")

    # 5. Direction bias from recent picks on this market
    recent_picks = _recent_outcomes(market_id, 5)
    if recent_picks:
        wins = sum(1 for r in recent_picks if r.get("won"))
        total = len(recent_picks)
        if total >= 2:
            signals.append(
                f"[MATH-HISTORY] {wins}/{total} wins on this market, "
                f"last direction: {recent_picks[0].get('direction', '?')}")

    # 6. Scout direction alignment
    if current_yes > 0.55 and scout_direction == "BUY_YES":
        signals.append("[MATH-ALIGN] Market already leans YES — BUY_YES means smaller upside")
    elif current_yes < 0.45 and scout_direction == "BUY_NO":
        signals.append("[MATH-ALIGN] Market already leans NO — BUY_NO means smaller upside")

    return signals


def _last_price(market_id: str) -> dict | None:
    """Get the most recent price for a market from the ledger."""
    try:
        with store._connect() as conn:
            row = conn.execute(
                "SELECT market_yes_price AS yes_price, ts FROM picks "
                "WHERE market_id=? AND arm='TEAM' ORDER BY id DESC LIMIT 1",
                (market_id,)
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _recent_outcomes(market_id: str, limit: int = 5) -> list[dict]:
    """Get recent pick outcomes for a market."""
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT direction, outcome, exit_price, market_yes_price, market_no_price FROM picks "
                "WHERE market_id=? AND arm='TEAM' AND exit_ts IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (market_id, limit)
            ).fetchall()
            results = []
            for r in rows:
                direction = r["direction"]
                entry = r["market_yes_price"] if direction == "BUY_YES" else r["market_no_price"]
                exit_px = r["exit_price"]
                won = (exit_px > entry) if (exit_px is not None and entry is not None and entry > 0) else None
                results.append({"direction": direction, "won": won})
            return results
    except Exception:
        return []


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
