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
                else:
                    log.debug("Grader: no picks to grade")
            except Exception as exc:
                log.error("grader error: %s", exc)
            await asyncio.sleep(3600)

    async def _autorun_loop():
        from datetime import datetime, timedelta
        from altavela.config import AUTORUN_END_ET, AUTORUN_INTERVAL_HOURS, AUTORUN_START_ET
        from altavela.ledger import store
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
                now_tup = (now.hour, now.minute)
                # Handle cross-midnight windows (e.g., 22:00-02:00)
                if (s_h, s_m) <= (e_h, e_m):
                    in_window = (s_h, s_m) <= now_tup < (e_h, e_m)
                else:
                    in_window = now_tup >= (s_h, s_m) or now_tup < (e_h, e_m)
                # Compute CURRENT slot (last interval boundary) — not NEXT.
                # The old +1 always pointed to the future slot, so the autorun never fired.
                window_start = now.replace(hour=s_h, minute=s_m, second=0, microsecond=0)
                mins_since_start = (now - window_start).total_seconds() / 60
                interval_min = AUTORUN_INTERVAL_HOURS * 60
                elapsed = int(mins_since_start / interval_min)
                current_slot = window_start + timedelta(minutes=elapsed * interval_min)

                if in_window and not running and now >= current_slot:
                    # Restart-safe: check if THIS slot already ran
                    lt = store.last_run_time("DESK")
                    last_slot = None
                    if lt:
                        try:
                            last_dt = datetime.fromisoformat(lt).astimezone(ET)
                            mins = (last_dt - window_start).total_seconds() / 60
                            last_slot = window_start + timedelta(minutes=int(mins / interval_min) * interval_min)
                        except (ValueError, TypeError):
                            pass
                    if last_slot is None or last_slot < current_slot:
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


def _register_stream(market_ids: list[str], registered: set[str]) -> None:
    """Look up CLOB token IDs for new markets and add them to the stream."""
    from altavela.ingest.polymarket import market_detail
    from altavela.ingest.stream import start_stream, _token_map, _token_lock, _stream_thread

    with _token_lock:
        current_map = dict(_token_map)

    for mid in market_ids:
        if mid in registered:
            continue
        try:
            detail = market_detail(mid)
        except Exception:
            continue
        if not detail:
            continue
        clob_ids = detail.get("clobTokenIds") or detail.get("clob_token_ids") or []
        if len(clob_ids) >= 2:
            current_map[clob_ids[0]] = {"market_id": mid, "side": "yes"}
            current_map[clob_ids[1]] = {"market_id": mid, "side": "no"}
            registered.add(mid)

    with _token_lock:
        old = dict(_token_map)

    if current_map != old:
        if not _stream_thread or not _stream_thread.is_alive():
            start_stream(current_map)
        else:
            # Thread alive — update token map directly, get_prices handles new tokens
            with _token_lock:
                _token_map.clear()
                _token_map.update(current_map)
        logging.getLogger("altavela.watch").info(
            "Stream registered %d tokens for %d markets",
            len(current_map), len(registered))

_profit_exit_markets: dict[str, str] = {}  # mid -> direction exited profitably

async def _watcher_loop():
    """Watch open positions — three exit triggers, checked every 60s:
    1. Trailing stop — activates at +TAKE_PROFIT_PCT%, trails TRAIL_PCT% below peak
    2. Stop loss — exits at entry × (1 - STOP_PCT/100)
    3. Stale exit — 4h+ open with <1% movement
    4. Market resolved — YES ≤0.001 or ≥0.999 → WIN/LOSS stamped
    5. Pre-game — sports markets 30min before kickoff"""
    from altavela.ingest.polymarket import live_prices, market_detail
    from altavela.ledger import store
    from altavela.config import WATCHER_TAKE_PROFIT_PCT, WATCHER_TRAIL_PCT, WATCHER_STALE_HOURS, WATCHER_STALE_MOVE_PCT, WATCHER_STOP_PCT, WATCHER_INTERVAL_S
    import time

    loop = asyncio.get_running_loop()
    log_w = logging.getLogger("altavela.watch")

    _trail: dict[int, float] = {}
    _registered: set[str] = set()
    _poll_cache: dict[str, tuple[float, float, float]] = {}  # mid -> (ts, yes_px, no_px)
    _POLL_INTERVAL = 5  # seconds between API polls for non-streamed markets
    _exited_markets: set[str] = set()  # markets where a position just exited profitably  # market_ids already in the stream

    # Try to use streaming for real-time prices
    _use_stream = False
    stream_prices = None
    try:
        from altavela.ingest.stream import get_prices as _stream_get_prices, start_stream
        stream_prices = _stream_get_prices
        _use_stream = True
        log_w.info("Using WebSocket streaming for live prices")
    except ImportError:
        log_w.info("WebSocket streaming not available — using API polling")

    while True:
        try:
            picks = await loop.run_in_executor(None, store.live_picks)
            _exited_markets.clear()  # always clean — even if no picks
            if picks:
                mids = list({p["market_id"] for p in picks if p.get("market_id")})
                # Register new markets with the stream
                if _use_stream:
                    new_mids = [m for m in mids if m not in _registered]
                    if new_mids:
                        _register_stream(new_mids, _registered)
                    prices = stream_prices(mids)
                    # Non-streamed markets: poll API every POLL_INTERVAL seconds
                    now_sec = time.time()
                    stale_poll = [m for m in mids if m not in prices and
                                  (m not in _poll_cache or now_sec - _poll_cache[m][0] >= _POLL_INTERVAL)]
                    if stale_poll:
                        api_prices = await loop.run_in_executor(None, live_prices, stale_poll)
                        prices.update(api_prices)
                        for m, px in api_prices.items():
                            _poll_cache[m] = (now_sec, px[0], px[1])
                    # Still missing: use cached values
                    for m in mids:
                        if m not in prices and m in _poll_cache:
                            _, y, n = _poll_cache[m]
                            prices[m] = (y, n)
                else:
                    prices = await loop.run_in_executor(None, live_prices, mids) if mids else {}

                for p in picks:
                    pid = p["id"]
                    mid = p.get("market_id", "")
                    if mid not in prices:
                        continue

                    direction = p.get("direction", "")
                    yes_px, no_px = prices[mid]
                    entry = p.get("market_yes_price") if direction == "BUY_YES" else p.get("market_no_price")

                    if not entry or entry <= 0:
                        continue

                    if direction == "BUY_YES":
                        cur = yes_px
                    else:
                        cur = no_px

                    tp = round(entry * (1 + WATCHER_TAKE_PROFIT_PCT / 100), 4)
                    stop = round(entry * (1 - WATCHER_STOP_PCT / 100), 4)
                    if stop < 0.01:
                        stop = 0.01  # floor at 1 cent, won't fire on <1 cent prices

                    # Trailing stop: activate once price passes TP, track peak,
                    # exit when price drops TRAIL_PCT below peak
                    trail_high = _trail.get(pid)
                    if cur >= tp:
                        if trail_high is None or cur > trail_high:
                            _trail[pid] = cur
                            trail_high = cur

                    reason = None
                    exit_px = None
                    # Market resolved: price ≤0.001 or ≥0.999 — the event is over
                    if yes_px <= 0.001 or yes_px >= 0.999:
                        if direction == "BUY_YES":
                            won = yes_px >= 0.999
                        else:
                            won = yes_px <= 0.001
                        reason = f"market resolved: {'WIN' if won else 'LOSS'} (YES={yes_px})"
                        exit_px = cur

                    # Pre-game exit: sports markets about to start — avoid live volatility
                    end_date = p.get("market_end_date", "")
                    if not reason and end_date:
                        from datetime import datetime, timezone
                        try:
                            dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                            mins_to_start = (dt - datetime.now(timezone.utc)).total_seconds() / 60
                            if 0 <= mins_to_start <= 30:
                                reason = f"pre-game exit: match starts in {mins_to_start:.0f}min"
                                exit_px = cur
                        except (ValueError, TypeError):
                            pass

                    if not reason and trail_high and cur <= trail_high * (1 - WATCHER_TRAIL_PCT / 100):
                        peak = trail_high
                        reason = f"trailing-stop: price {cur} fell {WATCHER_TRAIL_PCT}% below peak {peak}"
                        exit_px = cur
                    elif not reason and cur <= stop:
                        reason = f"stopped out: price {cur} fell below stop {stop}"
                        exit_px = cur

                    # Stale position — no meaningful movement after 4 hours
                    ts = p.get("ts", "")
                    if not reason and ts:
                        from datetime import datetime, timezone
                        try:
                            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
                            if age_h >= WATCHER_STALE_HOURS and abs((cur - entry) / entry * 100) < WATCHER_STALE_MOVE_PCT:
                                reason = f"stale: no movement after {age_h:.0f}h"
                                exit_px = cur
                        except (ValueError, TypeError):
                            pass

                    # Cluster exit: another position on this market just exited with profit
                    if not reason and mid in _exited_markets:
                        reason = f"cluster exit: market {mid} exited profitably"
                        exit_px = cur

                    if reason:
                        _trail.pop(pid, None)
                        pnl = (exit_px - entry) / entry * 100

                        await loop.run_in_executor(
                            None, lambda pid=pid, r=reason, px=exit_px:
                            store.record_exit(pid, r, px))
                        # If market resolved, mark the outcome too
                        if "market resolved" in reason:
                            outcome = 1.0 if "WIN" in reason else 0.0
                            await loop.run_in_executor(
                                None, lambda pid=pid, o=outcome, pnl_co=pnl:
                                store.mark_resolved(pid, o, pnl_pct=pnl_co))
                        log_w.info("Exit #%d %s %s: %s (pnl %+.1f%%)",
                                   pid, direction, p.get("question", "?")[:50],
                                   reason, pnl)
                        # Cluster exit: if we took profit on this market, flag it
                        if pnl > 0:
                            global _profit_exit_markets
                            _exited_markets.add(mid)
                            _profit_exit_markets[mid] = direction

            # Clean up stale trail entries and poll cache
            live_ids = {p["id"] for p in picks}
            live_mids = {p["market_id"] for p in picks if p.get("market_id")}
            for pid in list(_trail):
                if pid not in live_ids:
                    _trail.pop(pid, None)
            for mid in list(_poll_cache):
                if mid not in live_mids:
                    _poll_cache.pop(mid, None)
        except Exception as exc:
            log_w.error("watcher error: %s", exc)
        interval = 2 if _use_stream else WATCHER_INTERVAL_S
        await asyncio.sleep(interval)   # 2s with streaming, configured otherwise


async def _desk() -> None:
    """One-shot run: fetch markets, scout, debate, write to ledger."""
    from altavela.config import REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT
    from altavela.ingest.polymarket import fetch_markets, quality_filter
    from altavela.desk.scout import run_scout
    from altavela.ledger import store

    log = logging.getLogger("altavela.desk")

    log.info("Fetching active prediction markets…")
    markets = fetch_markets(limit=200, min_volume=10000)
    if not markets:
        log.info("No active markets found")
        return

    markets = quality_filter(markets)
    if not markets:
        log.info("No markets passed quality filter")
        return

    # Filter: skip markets debated recently unless price moved significantly
    recent = store.markets_debated_since(REPICK_COOLDOWN_HOURS)
    global _profit_exit_markets
    fresh_markets = []
    skipped_cooldown = 0
    for m in markets:
        mid = m.get("id", "")
        if mid in recent:
            prev = recent[mid]
            prices = m.get("prices", [0.5, 0.5])
            cur_yes = prices[0] if len(prices) > 0 else 0.5
            prev_yes = prev.get("yes_price") if prev.get("yes_price") is not None else 0.5
            if prev_yes > 0:
                move = abs(cur_yes - prev_yes) / prev_yes * 100
                if move < REPICK_MIN_PRICE_MOVE_PCT:
                    skipped_cooldown += 1
                    continue
        fresh_markets.append(m)

    if skipped_cooldown:
        log.info("Cooldown: %d markets skipped (debated <%.0fh, price move <%.0f%%), %d fresh",
             skipped_cooldown, REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT,
             len(fresh_markets))

    if not fresh_markets:
        log.info("No fresh markets after cooldown filter")
        return

    store.add_run("DESK")
    log.info("Signal scanning %d markets…", len(fresh_markets))

    # Math-driven: score markets by velocity + uncertainty, no LLM scout
    from altavela.desk.math import compute_signals
    from altavela.ingest.evidence import gather_evidence
    from altavela.desk.signal import sanity_check

    scored = []
    loop = asyncio.get_running_loop()
    for m in fresh_markets:
        prices = m.get("prices", [0.5, 0.5])
        yes_px = prices[0] if len(prices) > 0 else 0.5
        no_px = prices[1] if len(prices) > 1 else 0.5
        # Direction: momentum-driven. Positive velocity → BUY_YES, negative → BUY_NO
        direction = "BUY_YES" if yes_px >= 0.5 else "BUY_NO"
        
        # Block post-profit same-direction
        profit_dir = _profit_exit_markets.get(m["id"], "")
        if profit_dir and profit_dir == direction:
            continue

        signals = compute_signals(m["id"], yes_px, no_px, m.get("volume", 0),
                                 m.get("end_date", ""), direction)
        # Score: velocity magnitude + uncertainty bonus (near 0.50 = higher score)
        velocity_score = sum(1 for s in signals if "VELOCITY" in s and "flat" not in s) * 3
        uncertainty = abs(yes_px - 0.5)
        uncertainty_score = (0.25 - uncertainty) * 20  # max at 0.50, zero at 0.25 away
        score = velocity_score + max(0, uncertainty_score)
        if score > 0:
            scored.append((m, direction, signals, score))

    scored.sort(key=lambda x: x[3], reverse=True)
    picks = scored[:MAX_PICKS_PER_WINDOW]

    if not picks:
        log.info("No signal-worthy markets found")
        return

    log.info("Signal picked %d markets for sanity check", len(picks))

    for market, direction, math_signals, score in picks:
        try:
            mid = market["id"]
            evidence = await loop.run_in_executor(
                None, gather_evidence, market["question"], market)
            evidence.extend(math_signals)

            # LLM sanity check only — does evidence contradict the math?
            entry_px = market["prices"][0] if direction == "BUY_YES" else market["prices"][1] if len(market.get("prices", [])) > 1 else 0.5
            check = await loop.run_in_executor(
                None, lambda: sanity_check(
                    market["question"], direction, entry_px, math_signals, evidence))
            
            if not check.get("approve", True):
                log.info("Sanity REJECTED: %s — %s", market["question"][:60], check.get("reason", ""))
                continue
            log.info("Sanity APPROVED: %s — %s", market["question"][:60], check.get("reason", "signal valid"))

            # Book directly — no debate needed
            prices = market.get("prices", [0.5, 0.5])
            est_prob = float(abs(0.5 - entry_px)) / 0.5 * 100  # rough accuracy from distance
            pick_id = await loop.run_in_executor(
                None, lambda: store.record_pick({
                    "market_id": market["id"],
                    "question": market["question"],
                    "arm": "TEAM",
                    "edge": "MATH",
                    "trigger_src": "DESK",
                    "direction": direction,
                    "est_probability": round(abs(entry_px - 0.5) + 0.5, 2),
                    "score": round(min(score * 10, 100), 1),
                    "adjusted_score": round(min(score * 10, 100), 1),
                    "confidence": round(min(score * 10, 100), 1),
                    "verdict": "STRONG" if score > 5 else "SOFT",
                    "approved": 1,
                    "triage_reason": f"Signal score {score:.1f} — math-driven",
                    "thesis": check.get("reason", "Math signal"),
                    "debate": {"method": "signal", "score": score, "sanity": check.get("reason", "")},
                    "model_tags": {"researcher": "signal"},
                    "market_yes_price": prices[0] if len(prices) > 0 else 0.5,
                    "market_no_price": prices[1] if len(prices) > 1 else 0.5,
                    "market_volume": market.get("volume", 0),
                    "market_liquidity": market.get("liquidity", 0),
                    "market_end_date": market.get("end_date", ""),
                    "taken": 1,
                }))
            log.info("Signal booked #%d: %s %s (score=%.1f)", pick_id, direction,
                     market["question"][:60], score)
        except Exception as exc:
            log.warning("Signal pick failed for '%s': %s", market["question"][:60], exc)

    s = store.stats()
    log.info("Run complete: %d picks debated — %d open · %d closed · %d wins (%.1f%%) · P&L total=%+.1f%% median=%+.1f%%",
             len(picks), s["total_picks"] - s["closed"], s["closed"], s["closed_wins"],
             s["closed_win_rate"] or 0, s["total_pnl_pct"], s["median_pnl_pct"])


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
