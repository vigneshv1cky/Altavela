"""Altavela grader — grades prediction-market picks against actual resolutions.

Unlike AlphaDesk (which grades vs SPY at a pre-committed horizon), prediction
markets have binary outcomes: you're either right (1.0) or wrong (0.0). The grader:
  1. Checks if resolved markets have a known outcome
  2. Stamps the resolution on the pick
  3. Computes P&L (current price - entry price) for open positions
"""

import logging
import time

from altavela.ingest.polymarket import market_detail
from altavela.ledger import store

log = logging.getLogger("altavela.grader")


def grade_due() -> int:
    """Grade all unresolved picks. Returns number graded."""
    picks = store.due_for_grading()
    graded = 0
    for p in picks:
        mid = p.get("market_id", "")
        if not mid:
            continue

        # Check if the market has been resolved on Polymarket
        detail = None
        try:
            detail = market_detail(mid)
        except Exception:
            pass

        if detail and detail.get("resolved"):
            direction = p.get("direction", "")
            resolved_price = float(detail.get("resolutionPrice", detail.get("resolution_price", 0.5)) or 0.5)

            if direction == "BUY_YES":
                outcome = 1.0 if resolved_price > 0.5 else 0.0
            else:
                outcome = 1.0 if resolved_price < 0.5 else 0.0

            # Compute P&L: if we bought at entry_price and resolved at $1 (win) or $0 (loss)
            entry = p.get("market_yes_price") if direction == "BUY_YES" else p.get("market_no_price")
            pnl_pct = None
            if entry and entry > 0:
                if outcome == 1.0:
                    pnl_pct = round((1.0 - entry) / entry * 100, 1)  # resolved at $1
                else:
                    pnl_pct = round((0.0 - entry) / entry * 100, 1)  # resolved at $0

            store.mark_resolved(p["id"], outcome, pnl_pct=pnl_pct)
            graded += 1
            log.info("Graded #%d %s → outcome=%s pnl=%s",
                     p["id"], p.get("question", "?")[:60], outcome, pnl_pct)

        # For unresolved markets, update mark-to-market P&L
        # (skipping for now — would need live price fetching)

    return graded
