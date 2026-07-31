"""Quant arm — statistical prediction-market trading. Zero LLM calls.

Usage:
    python -m altavela.quant.main run       # one-shot scan + book
    python -m altavela.quant.main loop      # continuous loop (15min intervals)
"""

import argparse
import asyncio
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("altavela.quant")


async def _run_once(store, max_picks: int = 5) -> int:
    """One-shot: fetch markets, score, book top picks. Returns count booked."""
    from altavela.ingest.polymarket import fetch_markets, quality_filter
    from altavela.quant.scanner import scan
    import altavela.util as util

    log.info("Fetching markets…")
    markets = quality_filter(fetch_markets(limit=100, min_volume=10000))

    # In-match filter — same as desk
    from altavela.main import _mins_to_resolution
    filtered = []
    skipped = 0
    for m in markets:
        mins = _mins_to_resolution(m.get("end_date", ""))
        if mins is not None and mins <= 120:
            skipped += 1
            continue
        filtered.append(m)
    if skipped:
        log.info("In-match: %d blocked, %d passed", skipped, len(filtered))
    markets = filtered

    if not markets:
        log.info("No markets after filters")
        return 0

    # Cooldown filter
    from altavela.config import REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT
    recent = store.markets_debated_since(REPICK_COOLDOWN_HOURS)
    markets, skipped = util.apply_cooldown_filter(
        markets, recent, REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT)
    if skipped:
        log.info("Cooldown: %d skipped, %d passed", skipped, len(markets))

    if not markets:
        log.info("No markets after cooldown")
        return 0

    log.info("Scanning %d markets with statistical signals…", len(markets))
    picks = scan(markets, max_picks=max_picks)
    if not picks:
        log.info("No picks above threshold")
        return 0

    log.info("Top %d picks:", len(picks))
    booked = 0
    for p in picks:
        # Entry gate check
        prices = [p["market_yes_price"], p["market_no_price"]]
        from altavela.main import _entry_gate
        allow, reason = _entry_gate(p["market_id"], p["direction"], prices, store.live_picks)
        if not allow:
            log.info("  SKIP %s: %s", p["question"][:60], reason)
            continue

        pid = store.record_pick({
            "market_id": p["market_id"],
            "question": p["question"],
            "arm": "QUANT",
            "edge": "MOMENTUM",
            "trigger_src": "SCAN",
            "direction": p["direction"],
            "est_probability": round(p["market_yes_price"] if p["direction"] == "BUY_YES" else p["market_no_price"], 4),
            "score": p["score"],
            "adjusted_score": p["score"],
            "confidence": p["score"],
            "verdict": "STRONG" if p["score"] > 60 else "SOFT" if p["score"] > 30 else "PASS",
            "approved": 1 if p["score"] > 20 else 0,
            "triage_reason": f"quant: composite={p['composite']:.1f} {p['signals']}",
            "thesis": f"Statistical entry: {p['direction']} score={p['score']} composite={p['composite']:.1f}",
            "model_tags": {"quant": str(p["signals"])},
            "market_yes_price": p["market_yes_price"],
            "market_no_price": p["market_no_price"],
            "market_volume": p.get("market_volume", 0),
            "market_liquidity": p.get("market_liquidity", 0),
            "market_end_date": p.get("market_end_date", ""),
            "taken": 1,
        })
        booked += 1
        sign = "+" if p["composite"] > 0 else ""
        log.info("  #%d %s %s score=%s %s%.1f",
                 pid, p["direction"], p["question"][:50], p["score"], sign, p["composite"])

    store.add_run("QUANT")
    return booked


async def _loop():
    """Continuous quant scanning loop."""
    from altavela.ledger import store as ledger_store
    log.info("Quant arm running — scanning every 15 min")
    while True:
        try:
            n = await _run_once(ledger_store, max_picks=5)
            if n:
                log.info("Quant run: %d booked", n)
            else:
                log.debug("Quant run: no picks")
        except Exception as exc:
            log.error("Quant run error: %s", exc)
        await asyncio.sleep(900)  # 15 min


def main():
    p = argparse.ArgumentParser(description="Altavela Quant — statistical trading arm")
    sp = p.add_subparsers(dest="cmd")
    sp.add_parser("run", help="one-shot scan and book")
    sp.add_parser("loop", help="continuous loop (15 min intervals)")

    args = p.parse_args()
    if args.cmd == "run":
        from altavela.ledger import store
        n = asyncio.run(_run_once(store))
        s = store.stats()
        print(f"Booked: {n}")
        print(f"Total picks: {s['total_picks']} | Closed: {s['closed']} | Win rate: {s.get('closed_win_rate', '—')}%")
    elif args.cmd == "loop":
        asyncio.run(_loop())
    else:
        p.print_help()


if __name__ == "__main__":
    main()
