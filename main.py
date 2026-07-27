"""Altavela CLI — prediction-market research engine."""

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


async def _serve() -> None:
    from altavela.app.dashboard import create_app
    from altavela.config import DASHBOARD_HOST, DASHBOARD_PORT

    app = create_app()
    import uvicorn
    config = uvicorn.Config(app, host=DASHBOARD_HOST, port=DASHBOARD_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def _desk() -> None:
    """One-shot run: fetch markets, scout, debate, write to ledger."""
    from altavela.ingest.polymarket import fetch_markets
    from altavela.desk.scout import run_scout
    from altavela.ledger import store

    log = logging.getLogger("altavela.desk")

    log.info("Fetching active prediction markets…")
    markets = fetch_markets(limit=50, min_volume=1000)

    if not markets:
        log.info("No active markets found")
        return

    log.info("Scout scanning %d markets…", len(markets))
    result = run_scout(markets)
    picks = result.get("picks", [])

    if not picks:
        log.info("Scout found nothing worth debating (%d skips)", len(result.get("skips", [])))
        return

    log.info("Scout picked %d markets for debate", len(picks))

    # Simple sequential debate (no streaming — headless mode)
    for pick in picks:
        mid = pick["market_id"]
        market = next((m for m in markets if m["id"] == mid), {})
        if not market:
            continue

        log.info("Debating: %s", pick["question"][:80])
        async for ev in _debate_one(market, pick):
            t = ev.get("type", "")
            if t == "thesis":
                log.info("  Thesis: %s score=%s", ev.get("direction"), ev.get("score"))
            elif t == "decision":
                log.info("  Verdict: %s approved=%s score=%s flipped=%s",
                         ev.get("direction"), ev.get("approved"),
                         ev.get("adjusted_score"), ev.get("flipped"))
            elif t == "_result":
                log.info("  Booked #%d", ev.get("pick_id"))

    store.add_run("DESK")
    log.info("Run complete: %d picks debated", len(picks))


async def _debate_one(market, pick):
    from altavela.desk.debate import deliberate
    async for ev in deliberate(market, pick, [], "DESK", None):
        yield ev


def _status() -> None:
    from altavela.ledger import store
    s = store.stats()
    print(f"Total picks: {s['total_picks']}")
    print(f"Resolved: {s['resolved']}")
    print(f"Wins: {s['wins']}")
    if s['win_rate'] is not None:
        print(f"Win rate: {s['win_rate']}%")


def main() -> None:
    p = argparse.ArgumentParser(description="Altavela — prediction-market research engine")
    sp = p.add_subparsers(dest="cmd")

    sp.add_parser("dashboard", help="web dashboard")
    sp.add_parser("desk", help="one-shot headless run")
    sp.add_parser("status", help="ledger stats")

    args = p.parse_args()

    if args.cmd == "dashboard":
        asyncio.run(_serve())
    elif args.cmd == "desk":
        asyncio.run(_desk())
    elif args.cmd == "status":
        _status()
    else:
        p.print_help()


if __name__ == "__main__":
    main()
