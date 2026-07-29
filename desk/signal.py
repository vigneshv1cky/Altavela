"""Signal-based market selection — math picks, LLM sanity-checks.

Replaces the full debate pipeline for trading mode.
"""

import logging

from altavela.config import MAX_PICKS_PER_WINDOW
from altavela.llm import call_role

log = logging.getLogger("altavela.signal")

_SANITY_SYSTEM = (
    "You are a trading assistant. You receive a market proposal based on "
    "mathematical signals (price velocity, uncertainty, time to expiry, volume). "
    "Your ONLY job: read the evidence and decide if it CONTRADICTS the proposal.\n\n"
    "  • approve: the evidence does NOT contradict the math. Trade proceeds.\n"
    "  • reject: the evidence strongly contradicts the math (e.g., news just broke "
    "that changes everything, or the math signals are clearly stale).\n\n"
    "Default to APPROVE unless the evidence clearly says otherwise. "
    "The math already did the hard work — you're just catching obvious errors.\n\n"
    'Return ONLY JSON: {{"approve": true|false, "reason": "..."}}'
)

_SANITY_SCHEMA = {
    "approve": {"type": bool},
    "reason": {"type": str, "maxlen": 200},
}


def sanity_check(question: str, direction: str, entry_price: float,
                  signals: list[str], evidence: list[str]) -> dict:
    """Run LLM sanity check — approve or reject the math signal."""
    signal_str = "\n".join(f"- {s}" for s in signals[:6]) if signals else "none"
    ev_str = "\n".join(f"- {e}" for e in evidence[:8]) if evidence else "none"

    user = (
        f"PROPOSAL: {direction} on '{question}' at price {entry_price}\n\n"
        f"MATH SIGNALS:\n{signal_str}\n\n"
        f"EVIDENCE:\n{ev_str}\n\n"
        f"Does the evidence CONTRADICT this proposal? Approve or reject."
    )
    return call_role("researcher", _SANITY_SYSTEM, user, schema=_SANITY_SCHEMA)
