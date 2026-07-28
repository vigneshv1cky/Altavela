"""Paper trading broker — simulates Polymarket CLOB fills locally.

Records paper positions with sizing, enforces limits, tracks fills."""

import logging
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _category_positions(category: str) -> int:
    """Count open positions in the same category."""
    if not category:
        return 0
    picks = store.live_picks()
    count = 0
    for p in picks:
        if p.get("market_category") == category:
            count += 1
    return count


def _open_positions() -> int:
    return len(store.live_picks())


def execute_pick(pick_id: int, pick: dict, market: dict, plan: dict) -> bool:
    """Paper-trade a pick: compute size, check limits, record fill.

    Returns True if executed, False if skipped.
    """
    if not PAPER_TRADING:
        return False

    if not plan.get("approved"):
        log.info("Paper broker: plan rejected #%d — %s", pick_id, plan.get("reasoning", ""))
        return False

    cat_positions = _category_positions(market.get("category", ""))
    open_pos = _open_positions()

    if open_pos >= PM_MAX_POSITIONS:
        log.warning("Paper broker: max positions (%d) reached — skipping #%d",
                     PM_MAX_POSITIONS, pick_id)
        return False

    if cat_positions >= CONCENTRATION_MAX_PER_CATEGORY:
        log.warning("Paper broker: category cap (%d) reached — skipping #%d",
                     CONCENTRATION_MAX_PER_CATEGORY, pick_id)
        return False

    # Size: fraction of PM_MAX_POSITION_USD, floored at PM_BASE_USD
    fraction = float(plan.get("size_fraction", 0.5))
    if fraction <= 0:
        return False

    size_usd = round(max(PM_BASE_USD, PM_MAX_POSITION_USD * fraction), 2)
    direction = pick.get("direction", "BUY_YES")
    price = pick.get("market_yes_price") if direction == "BUY_YES" else pick.get("market_no_price")
    if not price or price <= 0:
        price = 0.5

    qty = round(size_usd / price, 2)

    store.update_pick(pick_id,
                      broker_order_id=f"paper-{pick_id}",
                      broker_status="filled",
                      broker_qty=qty,
                      broker_fill_price=price,
                      broker_fill_ts=_now(),
                      position_size=size_usd,
                      taken=1)

    log.info("Paper broker: filled #%d — %s $%s @ $%s (%s)",
             pick_id, direction, size_usd, price, f"{fraction:.0%}")

    return True
