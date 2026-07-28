"""Paper broker — simulated Polymarket CLOB execution for research tracking.

Enabled with PAPER_TRADING=1. Does NOT place real on-chain orders.
Sizes positions by conviction, enforces position/concentration limits,
and records simulated fills in the ledger.
"""

import logging
import uuid
from datetime import datetime, timezone

from altavela.config import (
    CONCENTRATION_MAX_PER_CATEGORY,
    PAPER_TRADING,
    PM_BASE_USD,
    PM_MAX_POSITION_USD,
    PM_MAX_POSITIONS,
)
from altavela.ledger import store

log = logging.getLogger("altavela.broker")

_CATEGORY_KEYWORDS = {
    "sports": ["sports", "nfl", "nba", "mlb", "nhl", "soccer", "tennis", "football",
               "basketball", "baseball", "boxing", "mma", "ufc", "cricket", "rugby",
               "league", "championship", "tournament", "grand prix", "f1", "nascar",
               "esports", "counter-strike", "lol", "dota", "valorant", "itf", "atp", "wta"],
    "crypto": ["bitcoin", "ethereum", "btc", "eth", "crypto", "solana", "xrp",
               "blockchain", "defi", "nft", "token"],
    "politics": ["trump", "biden", "democrat", "republican", "election", "senate",
                 "congress", "president", "governor", "vote", "political", "gop",
                 "democratic", "republicans", "democrats", "gaza", "israel", "iran",
                 "ukraine", "russia", "china", "tariff", "sanction"],
    "finance": ["s&p", "spy", "nasdaq", "dow", "stock", "gdp", "inflation", "fed",
                "rate", "bond", "yield", "gold", "xauusd", "oil", "commodity",
                "treasury", "dollar", "eurusd", "forex"],
    "entertainment": ["movie", "oscar", "film", "box office", "music", "album",
                      "artist", "grammy", "emmy", "tv", "show", "netflix", "disney",
                      "celebrity", "award", "concert", "tour"],
    "weather": ["temperature", "weather", "heat", "cold", "rain", "storm",
                "hurricane", "tornado", "climate"],
}


def _detect_category(market: dict) -> str:
    question = (market.get("question") or "").lower()
    tags = [t.lower() for t in (market.get("tags") or [])]
    combined = question + " " + " ".join(tags)

    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in combined:
                return cat
    return "other"


def _current_positions() -> list[dict]:
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM picks WHERE arm='TEAM' AND taken=1 AND exit_ts IS NULL"
            " AND resolved=0 ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def _category_count(cat: str) -> int:
    picks = _current_positions()
    # We need market data to detect categories. Use stored market tags or question.
    counts = {}
    for p in picks:
        c = _detect_category(p)
        counts[c] = counts.get(c, 0) + 1
    return counts.get(cat, 0)


def place_paper_order(pick_id: int, market: dict, direction: str,
                      entry_price: float, verdict: str, approved: bool) -> dict | None:
    """Simulate placing a paper order. Returns fill info or None if skipped."""
    if not PAPER_TRADING:
        return None

    # Skip if not approved
    if not approved:
        log.info("Paper: #%d skipped (not approved, verdict=%s)", pick_id, verdict)
        return None

    # Check position limit
    live = _current_positions()
    if len(live) >= PM_MAX_POSITIONS:
        log.info("Paper: #%d skipped (max %d positions)", pick_id, PM_MAX_POSITIONS)
        return None

    # Check concentration limit
    cat = _detect_category(market)
    cat_count = _category_count(cat)
    if cat_count >= CONCENTRATION_MAX_PER_CATEGORY:
        log.info("Paper: #%d skipped (max %d per category '%s', have %d)",
                 pick_id, CONCENTRATION_MAX_PER_CATEGORY, cat, cat_count)
        return None

    # Size by conviction
    conviction = verdict
    if conviction == "STRONG":
        size = PM_BASE_USD
    elif conviction == "SOFT":
        size = PM_BASE_USD / 2
    else:
        size = PM_BASE_USD / 4

    # Cap at max position size
    if size > PM_MAX_POSITION_USD:
        size = PM_MAX_POSITION_USD

    now = datetime.now(timezone.utc).isoformat()
    order_id = f"paper-{uuid.uuid4().hex[:8]}"

    store.update_pick(pick_id,
        broker_order_id=order_id,
        broker_status="filled",
        broker_qty=round(size / entry_price, 2) if entry_price > 0 else 0,
        broker_fill_price=round(entry_price, 4),
        broker_fill_ts=now,
        position_size=round(size, 2),
    )

    log.info("Paper: #%d filled %s %s %dx @ $%.4f = $%.2f (%s/%s, cat=%s)",
             pick_id, direction, verdict,
             round(size / entry_price, 2) if entry_price > 0 else 0,
             entry_price, size, verdict, conviction, cat)
    return {
        "order_id": order_id,
        "size_usd": round(size, 2),
        "qty": round(size / entry_price, 2) if entry_price > 0 else 0,
        "fill_price": round(entry_price, 4),
        "category": cat,
    }
