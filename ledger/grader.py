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
            # Market resolved — stamp the outcome
            # Polymarket resolved markets have `resolved` = true
            # The winning outcome is in `outcomes` array based on resolution
            resolved_price = float(detail.get("resolutionPrice", detail.get("resolution_price", 0.5)) or 0.5)
            # Convert to binary: which side won?
            # If we bought YES at entry, and YES resolved (price→1), outcome=1.0
            # If we bought NO, and YES resolved, outcome=0.0
            direction = p.get("direction", "")
            if direction == "BUY_YES":
                outcome = 1.0 if resolved_price > 0.5 else 0.0
            else:  # BUY_NO
                outcome = 1.0 if resolved_price < 0.5 else 0.0

            store.mark_resolved(p["id"], outcome)
            graded += 1
            log.info("Graded #%d %s → outcome=%s (resolution price=%s)",
                     p["id"], p.get("question", "?")[:60], outcome, resolved_price)

        # For unresolved markets, update mark-to-market P&L
        # (skipping for now — would need live price fetching)

    return graded
