"""Altavela — a multi-agent prediction-market research engine.

Scans Polymarket for binary outcome markets, debates true probabilities
via an LLM research team (scout → researcher → critic → rebuttal → judge),
and books directional calls (BUY YES / BUY NO).

Open positions are watched by a trailing stop / stop loss / stale exit watcher
every 60s. Resolved markets are graded against actual outcomes.

Research mode — no on-chain execution. Web dashboard at :8001.
"""

__version__ = "0.2.0"
