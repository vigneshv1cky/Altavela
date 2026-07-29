"""Signal + sanity check pipeline — math picks markets, LLM validates.

Replaces the full debate pipeline. Code computes math signals (velocity,
uncertainty, expiry, volume), ranks markets, and picks the top N. A single
LLM call validates that evidence doesn't contradict the math signal.
"""

import logging

from altavela.llm import call_role

log = logging.getLogger("altavela.signal")

_SANITY_SYSTEM = (
    "You are a trading circuit breaker. You receive a market proposal based "
    "on mathematical signals. Your ONLY job: check if evidence CONTRADICTS "
    "the proposal.\n\n"
    "  • approve: evidence does not contradict. Trade proceeds.\n"
    "  • reject: evidence clearly contradicts the math (breaking news, stale "
    "signals, obvious market change).\n\n"
    "Default to APPROVE. You are catching obvious errors, not re-analyzing. "
    "The math already did the work.\n\n"
    'Return ONLY JSON: {{"approve": true|false, "reason": "..."}}'
)

_SANITY_SCHEMA = {
    "approve": {"type": bool},
    "reason": {"type": str, "maxlen": 200},
}


def sanity_check(question: str, direction: str, entry_price: float,
                 signals: list[str], evidence: list[str]) -> dict:
    """Run LLM sanity check — approve or reject the math signal."""
    signal_str = "\n".join(signals[:6]) if signals else "no signals"
    ev_str = "\n".join(evidence[:8]) if evidence else "no evidence"

    user = (
        f"PROPOSAL: {direction} on '{question}' at {entry_price}\n\n"
        f"SIGNALS:\n{signal_str}\n\n"
        f"EVIDENCE:\n{ev_str}"
    )
    return call_role("researcher", _SANITY_SYSTEM, user, schema=_SANITY_SCHEMA)
