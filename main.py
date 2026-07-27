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
    """Dashboard + autorun loop + grader loop."""
    from altavela.app.dashboard import create_app
    from altavela.config import DASHBOARD_HOST, DASHBOARD_PORT

    # Always bind to all interfaces on a server, regardless of config default
    host = "0.0.0.0" if DASHBOARD_HOST == "127.0.0.1" else DASHBOARD_HOST
    app = create_app()

    async def _grader_loop():
        from altavela.ledger.grader import grade_due
        loop = asyncio.get_running_loop()
        log = logging.getLogger("altavela.grader")
        while True:
            try:
                n = await loop.run_in_executor(None, grade_due)
                if n:
                    log.info("Graded %d picks", n)
            except Exception as exc:
                log.error("grader error: %s", exc)
            await asyncio.sleep(3600)

    async def _autorun_loop():
        from datetime import datetime, timedelta
        from altavela.config import AUTORUN_END_ET, AUTORUN_INTERVAL_HOURS, AUTORUN_START_ET
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        log = logging.getLogger("altavela.autorun")

        if AUTORUN_INTERVAL_HOURS <= 0 or not AUTORUN_START_ET:
            log.info("Auto-run disabled")
            return
        try:
            s_h, s_m = (int(x) for x in AUTORUN_START_ET.split(":"))
            e_h, e_m = (int(x) for x in AUTORUN_END_ET.split(":"))
        except Exception:
            log.error("Bad AUTORUN_START/END_ET")
            return

        interval = timedelta(hours=AUTORUN_INTERVAL_HOURS)
        log.info("Auto-run: every %gh, %s–%s ET", AUTORUN_INTERVAL_HOURS, AUTORUN_START_ET, AUTORUN_END_ET)
        running = False
        while True:
            try:
                now = datetime.now(ET)
                in_window = ((now.hour, now.minute) >= (s_h, s_m) and
                             (now.hour, now.minute) < (e_h, e_m))
                mins_since_start = (now.hour - s_h) * 60 + (now.minute - s_m)
                next_slot_min = ((mins_since_start // int(AUTORUN_INTERVAL_HOURS * 60)) + 1) * int(AUTORUN_INTERVAL_HOURS * 60)
                next_slot = now.replace(hour=s_h + next_slot_min // 60,
                                        minute=next_slot_min % 60,
                                        second=0, microsecond=0)

                if in_window and not running and now >= next_slot:
                    running = True
                    try:
                        log.info("Auto-run: firing")
                        await _desk()
                        log.info("Auto-run complete")
                    finally:
                        running = False
            except Exception as exc:
                log.error("auto-run error: %s", exc)
            await asyncio.sleep(60)

    import uvicorn
    config = uvicorn.Config(app, host=host, port=DASHBOARD_PORT, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(_grader_loop(), _autorun_loop(), _watcher_loop(), server.serve())


async def _watcher_loop():
    """Watch open positions — exit when price reaches the researcher's estimated
    fair value (target) or falls past the stop-loss threshold."""
    from altavela.ingest.polymarket import live_prices
    from altavela.ledger import store

    loop = asyncio.get_running_loop()
    log_w = logging.getLogger("altavela.watch")

    while True:
        try:
            picks = await loop.run_in_executor(None, store.live_picks)
            if picks:
                mids = list({p["market_id"] for p in picks if p.get("market_id")})
                prices = await loop.run_in_executor(None, live_prices, mids) if mids else {}

                for p in picks:
                    mid = p.get("market_id", "")
                    if mid not in prices:
                        continue

                    direction = p.get("direction", "")
                    yes_px, no_px = prices[mid]
                    entry = p.get("market_yes_price") if direction == "BUY_YES" else p.get("market_no_price")
                    est_prob = p.get("est_probability") or 0.5

                    if not entry or entry <= 0:
                        continue

                    # Target = researcher's estimated fair value
                    # Stop = entry - max(entry * 0.25, 0.03) — 25% or 3 cents max loss
                    if direction == "BUY_YES":
                        target = min(est_prob, 0.99)
                        cur = yes_px
                    else:
                        target = min(1.0 - est_prob, 0.99)
                        cur = no_px

                    stop = max(entry - max(entry * 0.25, 0.03), 0.01)
                    # Sanity: stop must be below entry and target must be above entry
                    if stop >= entry or target <= entry:
                        continue

                    reason = None
                    exit_px = None
                    if cur >= target:
                        reason = f"target hit: price {cur} reached est fair value {target}"
                        exit_px = cur
                    elif cur <= stop:
                        reason = f"stopped out: price {cur} fell below stop {stop}"
                        exit_px = cur

                    if reason:
                        # P&L: return = (exit - entry) / entry for BUY_YES
                        # For BUY_NO: return = (entry - exit) / entry (profit when NO price goes up)
                        if direction == "BUY_YES":
                            pnl = (exit_px - entry) / entry * 100
                        else:
                            pnl = (entry - exit_px) / entry * 100

                        await loop.run_in_executor(
                            None, lambda pid=p["id"], r=reason, px=exit_px:
                            store.record_exit(pid, r, px))
                        log_w.info("Exit #%d %s %s: %s (pnl %+.1f%%)",
                                   p["id"], direction, p.get("question", "?")[:50],
                                   reason, pnl)
        except Exception as exc:
            log_w.error("watcher error: %s", exc)
        await asyncio.sleep(120)   # check every 2 min


async def _desk() -> None:
    """One-shot run: fetch markets, scout, debate, write to ledger."""
    from altavela.config import REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT
    from altavela.ingest.polymarket import fetch_markets, quality_filter
    from altavela.desk.scout import run_scout
    from altavela.ledger import store

    log = logging.getLogger("altavela.desk")

    log.info("Fetching active prediction markets…")
    markets = fetch_markets(limit=50, min_volume=5000)
    if not markets:
        log.info("No active markets found")
        return

    markets = quality_filter(markets)
    if not markets:
        log.info("No markets passed quality filter")
        return

    # Filter: skip markets debated recently unless price moved significantly
    recent = store.markets_debated_since(REPICK_COOLDOWN_HOURS)
    fresh_markets = []
    skipped_cooldown = 0
    for m in markets:
        mid = m.get("id", "")
        if mid in recent:
            prev = recent[mid]
            prices = m.get("prices", [0.5, 0.5])
            cur_yes = prices[0] if len(prices) > 0 else 0.5
            prev_yes = prev.get("yes_price") or 0.5
            if prev_yes > 0:
                move = abs(cur_yes - prev_yes) / prev_yes * 100
                if move < REPICK_MIN_PRICE_MOVE_PCT:
                    skipped_cooldown += 1
                    continue
        fresh_markets.append(m)

    if skipped_cooldown:
        log.info("Cooldown: %d markets skipped (debated <%.0fh, price move <%.0f%%)",
                 skipped_cooldown, REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT)

    if not fresh_markets:
        log.info("No fresh markets after cooldown filter")
        return

    log.info("Scout scanning %d markets (%d skipped by cooldown)…",
             len(fresh_markets), skipped_cooldown)
    result = run_scout(fresh_markets)
    picks = result.get("picks", [])

    if not picks:
        log.info("Scout found nothing worth debating (%d skips)", len(result.get("skips", [])))
        return

    log.info("Scout picked %d markets for debate", len(picks))

    # Simple sequential debate (no streaming — headless mode)
    from altavela.ingest.evidence import gather_evidence

    for pick in picks:
        mid = pick["market_id"]
        market = next((m for m in fresh_markets if m["id"] == mid), {})
        if not market:
            continue

        # Gather real-world evidence for the researcher
        loop = asyncio.get_running_loop()
        evidence = await loop.run_in_executor(
            None, gather_evidence, pick["question"], market.get("tags"))

        log.info("Debating: %s (%d evidence articles)", pick["question"][:80], len(evidence))
        async for ev in _debate_one(market, pick, evidence):
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


async def _debate_one(market, pick, evidence=None):
    from altavela.desk.debate import deliberate
    evidence = evidence or []
    async for ev in deliberate(market, pick, evidence, "DESK", None):
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
