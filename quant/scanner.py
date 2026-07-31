"""Quant scanner — ranks prediction markets by statistical signal strength.
No LLMs. Pure math. Same watcher handles exits.
"""

import logging

from altavela.quant.signals import compute_composite
from altavela.ledger import store

log = logging.getLogger("altavela.quant.scanner")


def scan(markets: list[dict], max_picks: int = 5) -> list[dict]:
    """Score and rank markets by composite statistical signal.
    Returns top N picks as [{market_id, question, direction, score, signals, ...}]."""

    scored = []
    for m in markets:
        mid = m.get("id", "")
        prices = m.get("prices", [0.5, 0.5])
        yes_px = prices[0] if len(prices) > 0 else 0.5
        no_px = prices[1] if len(prices) > 1 else 0.5
        volume = m.get("volume", 0)
        liquidity = m.get("liquidity", 0)
        end_date = m.get("end_date", "")

        # Get previous price from ledger for momentum calc
        prev_yes = None
        prev_age = 0
        try:
            prev = _last_price(mid)
            if prev:
                prev_yes = prev.get("yes_price")
                prev_ts = prev.get("ts", "")
                if prev_ts:
                    from datetime import datetime, timezone as tz
                    age = (datetime.now(tz.utc) - datetime.fromisoformat(prev_ts)).total_seconds() / 3600
                    prev_age = max(0, age)
        except Exception:
            pass

        result = compute_composite(
            market_id=mid,
            yes_price=yes_px,
            no_price=no_px,
            volume=volume,
            liquidity=liquidity,
            end_date=end_date,
            prev_yes=prev_yes,
            prev_age_hours=prev_age,
            ledger_store=store,
        )

        if result["score"] < 15:
            continue  # no conviction either way

        scored.append({
            "market_id": mid,
            "question": m.get("question", "")[:120],
            "direction": result["direction"],
            "score": result["score"],
            "composite": result["composite"],
            "signals": result["signals"],
            "market_yes_price": yes_px,
            "market_no_price": no_px,
            "market_volume": volume,
            "market_liquidity": liquidity,
            "market_end_date": end_date,
        })

    scored.sort(key=lambda x: abs(x["composite"]), reverse=True)
    return scored[:max_picks]


def _last_price(market_id: str) -> dict | None:
    try:
        with store._connect() as conn:
            row = conn.execute(
                "SELECT market_yes_price AS yes_price, ts FROM picks "
                "WHERE market_id=? ORDER BY id DESC LIMIT 1",
                (market_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None
