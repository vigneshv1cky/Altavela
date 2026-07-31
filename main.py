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
    """Dashboard + autorun loop + watcher loop."""
    from altavela.app.dashboard import create_app
    from altavela.config import DASHBOARD_HOST, DASHBOARD_PORT

    # Always bind to all interfaces on a server, regardless of config default
    host = "0.0.0.0" if DASHBOARD_HOST == "127.0.0.1" else DASHBOARD_HOST
    app = create_app()

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
    await asyncio.gather(_autorun_loop(), _watcher_loop(), server.serve())


def _register_stream(market_ids: list[str], registered: set[str]) -> None:
    """Look up CLOB token IDs for new markets, add them to the stream, and seed
    initial prices from REST so there's never a gap."""
    from altavela.ingest.polymarket import _parse_json_field, market_detail
    from altavela.ingest.stream import set_price, start_stream, _token_map, _token_lock, _stream_thread

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
            # Seed initial prices from REST while the stream catches up
            try:
                prices_raw = _parse_json_field(detail, "outcomePrices")
                if len(prices_raw) >= 2:
                    yes = round(float(prices_raw[0]), 4)
                    no = round(float(prices_raw[1]), 4)
                    set_price(mid, yes, no)
            except (ValueError, TypeError):
                pass

    with _token_lock:
        old = dict(_token_map)

        if current_map != old:
            if not _stream_thread or not _stream_thread.is_alive():
                # Release lock before starting stream (start_stream acquires it)
                needs_restart = True
            else:
                _token_map.clear()
                _token_map.update(current_map)
                needs_restart = False
        else:
            needs_restart = False

    if needs_restart:
        start_stream(current_map)

    if current_map != old:
        logging.getLogger("altavela.watch").info(
            "Stream registered %d tokens for %d markets",
            len(current_map), len(registered))

import altavela.util as util

_profit_exit_markets: dict[str, str] = {}  # mid -> direction exited profitably
_loss_exit_markets_global: dict[str, tuple[str, float]] = {}  # mid -> (direction, timestamp)


def _mins_to_resolution(end_date: str) -> float | None:
    """Minutes until a market resolves, or None if unparseable. Shared across
    in-match filter, pre-game exit, and late-game flip."""
    if not end_date:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        return (dt - datetime.now(timezone.utc)).total_seconds() / 60
    except (ValueError, TypeError):
        return None


import re as _re
_SPORTS_PATTERN = _re.compile(
    r"\b(win|lose|draw|match|game|tournament|championship|league|final|semi|quarter"
    r"|playoff|series|season|spread|home|away|score|goal|point|set"
    r"|nfl|nba|mlb|nhl|mls|epl|la.?liga|serie.?a|bundesliga|ligue"
    r"|ufc|wwe|f1|nascar|boxing|mma|cricket|rugby|tennis|golf"
    r"|counter.?strike|lol\b|dota|valorant|overwatch|esports"
    r"|itf|atp|wta|grand.?slam|vs\b|versus)\b", _re.IGNORECASE)


def _is_sports_question(question: str) -> bool:
    """Heuristic: does this market look like a sports/esports event?"""
    return bool(_SPORTS_PATTERN.search(question))


def _is_unusual_spike(store, mid: str, current_spike_pct: float) -> bool:
    """Check if the current spike is historically unusual for this market
    by comparing against ALL recorded prices (entry + exit) — not just spikes.
    Uses standard deviation: spike is unusual if the market
    has been stable historically and this move is large relative to its norm."""
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT market_yes_price, market_no_price, exit_price FROM picks"
                " WHERE market_id=? AND arm='TEAM'",
                (mid,),
            ).fetchall()
        prices = []
        for r in rows:
            for p in [r["market_yes_price"], r["market_no_price"], r["exit_price"]]:
                if p is not None and p > 0:
                    prices.append(p)
        if len(prices) < 5:
            return current_spike_pct > 30  # no history: assume big spike = unusual
        mean = sum(prices) / len(prices)
        std = (sum((p - mean) ** 2 for p in prices) / len(prices)) ** 0.5
        if std < 0.01:   # very stable market (e.g. "Will the sun rise?")
            return current_spike_pct > 10
        elif std < 0.05: # moderate volatility
            return current_spike_pct > 25
        else:            # naturally volatile market
            return current_spike_pct > 50
    except Exception:
        return False


def _entry_gate(mid: str, direction: str, prices: list[float],
                live_picks_fn) -> tuple[bool, str]:
    """Single entry gate — replaces post-profit, post-loss, and underwater checks.
    Returns (allow, reason_if_blocked)."""
    import time

    # Post-profit block — same dir already won
    profit_dir = _profit_exit_markets.get(mid, "")
    if profit_dir and profit_dir == direction:
        return False, f"post-profit block on {profit_dir}"

    # Post-loss block — same dir already lost (4h expiry)
    loss_entry = _loss_exit_markets_global.get(mid)
    if loss_entry:
        loss_dir, loss_ts = loss_entry
        if loss_dir == direction and time.time() - loss_ts < 14400:
            hrs_left = (14400 - (time.time() - loss_ts)) / 3600
            return False, f"post-loss block on {loss_dir} ({hrs_left:.0f}h left)"

    # Underwater — same-dir live position already losing
    for ep in live_picks_fn():
        if ep.get("market_id") == mid and ep.get("direction") == direction:
            cur = util.current_price(prices, direction)
            e_entry = util.entry_price(ep, direction)
            if e_entry and cur < e_entry:
                pct = (cur - e_entry) / e_entry * 100
                return False, f"existing #{ep['id']} underwater ({pct:.1f}%)"

    return True, ""

async def _fire_desk(log_w, trigger_mid: str, trigger_price: float) -> None:
    """Fire _desk() in background — event-driven on price swing."""
    try:
        log_w.info("Event-driven desk started (trigger: %s @ %.4f)", trigger_mid, trigger_price)
        await _desk()
        log_w.info("Event-driven desk complete")
    except Exception as exc:
        log_w.error("Event-driven desk failed: %s", exc)


async def _watcher_loop():
    """Watch open positions — three exit triggers, checked every 60s:
    1. Trailing stop — activates at +TAKE_PROFIT_PCT%, trails TRAIL_PCT% below peak
    2. Stop loss — exits at entry × (1 - STOP_PCT/100)
    3. Stale exit — 4h+ open with <1% movement
    4. Market resolved — YES ≤0.001 or ≥0.999 → WIN/LOSS stamped
    5. Pre-game — sports markets 30min before kickoff"""
    from altavela.ledger import store
    from altavela.config import WATCHER_TAKE_PROFIT_PCT, WATCHER_TRAIL_PCT, WATCHER_STALE_HOURS, WATCHER_STALE_MOVE_PCT, WATCHER_STOP_PCT, WATCHER_INTERVAL_S
    import os
    import time

    loop = asyncio.get_running_loop()
    log_w = logging.getLogger("altavela.watch")

    _trail: dict[int, float] = {}
    _spike_trail: dict[int, bool] = {}  # pid -> tight trail active
    _registered: set[str] = set()
    _exited_markets: set[str] = set()
    _loss_exit_markets: set[str] = set()
    _exited_pick_ids: set[int] = set()
    _resolution_checked: dict[str, float] = {}  # mid -> last API check timestamp
    _prev_prices: dict[str, float] = {}          # mid -> last known YES price
    _last_desk_fire = 0.0
    _DESK_SWING_PCT = float(os.environ.get("DESK_SWING_PCT", "8"))
    _DESK_COOLDOWN_S = float(os.environ.get("DESK_COOLDOWN_S", "300"))

    # Real-time prices via WebSocket stream
    stream_prices = None
    try:
        from altavela.ingest.stream import get_prices as _stream_get_prices, start_stream
        stream_prices = _stream_get_prices
        log_w.info("Using WebSocket streaming for live prices")
    except ImportError:
        log_w.error("WebSocket streaming not available — live prices unavailable")

    while True:
        try:
            picks = await loop.run_in_executor(None, store.live_picks)
            _exited_markets.clear()
            _loss_exit_markets.clear()
            _exited_pick_ids.clear()
            if picks:
                # Sort by timestamp — oldest positions on each market examined first.
                # This way an early loss shields later entries on the same market.
                picks.sort(key=lambda p: p.get("ts", ""))

                mids = list({p["market_id"] for p in picks if p.get("market_id")})
                # Register new markets with the stream
                if stream_prices:
                    new_mids = [m for m in mids if m not in _registered]
                    if new_mids:
                        _register_stream(new_mids, _registered)
                    prices = stream_prices(mids)
                else:
                    prices = {}

                # Event-driven desk: fire _desk() on big price swings
                if stream_prices and time.time() - _last_desk_fire >= _DESK_COOLDOWN_S:
                    for mid, (yes_px, _no_px) in prices.items():
                        prev = _prev_prices.get(mid)
                        # Update tracker regardless
                        _prev_prices[mid] = yes_px
                        if prev is None or prev <= 0 or yes_px <= 0:
                            continue
                        swing = abs(yes_px - prev) / prev * 100
                        if swing >= _DESK_SWING_PCT:
                            _last_desk_fire = time.time()
                            log_w.info("Swing %.1f%% on %s — firing event-driven desk", swing, mid)
                            asyncio.create_task(_fire_desk(log_w, mid, yes_px))
                            break  # fire once per cooldown window
                else:
                    # Still track prices for next comparison
                    for mid, (yes_px, _no_px) in prices.items():
                        _prev_prices[mid] = yes_px

                for p in picks:
                    pid = p["id"]
                    mid = p.get("market_id", "")
                    # Skip if a loss already exited on this market this cycle
                    if mid in _loss_exit_markets:
                        continue
                    if mid not in prices:
                        continue

                    direction = p.get("direction", "")
                    yes_px, no_px = prices[mid]
                    entry = util.entry_price(p, direction)
                    cur = util.current_price((yes_px, no_px), direction)

                    if not entry or entry <= 0:
                        continue

                    tp = round(entry * (1 + WATCHER_TAKE_PROFIT_PCT / 100), 4)
                    stop = round(entry * (1 - WATCHER_STOP_PCT / 100), 4)
                    if stop < 0.01:
                        stop = 0.01  # floor at 1 cent, won't fire on <1 cent prices

                    # Trailing stop: activate once price passes TP, track peak,
                    # exit when price drops TRAIL_PCT below peak.
                    # If this spike is historically unusual for the market, tighten.
                    trail_high = _trail.get(pid)
                    if cur >= tp:
                        if trail_high is None or cur > trail_high:
                            _trail[pid] = cur
                            trail_high = cur
                        # On first TP activation, check if this is an unusual spike
                        if pid not in _spike_trail:
                            spike_pct = (cur - entry) / entry * 100 if entry > 0 else 0
                            _spike_trail[pid] = _is_unusual_spike(store, mid, spike_pct)

                    reason = None
                    exit_px = None
                    resolved_outcome = None  # official outcome from Polymarket API
                    # Market resolved: price extreme → confirm with official API
                    if yes_px <= 0.001 or yes_px >= 0.999:
                        last_check = _resolution_checked.get(mid, 0)
                        if time.time() - last_check >= 300:  # query API at most every 5 min
                            from altavela.ingest.polymarket import market_detail
                            _resolution_checked[mid] = time.time()
                            try:
                                detail = await loop.run_in_executor(
                                    None, market_detail, mid)
                            except Exception:
                                detail = None
                            if detail and detail.get("resolved"):
                                rp = detail.get("resolutionPrice") or detail.get("resolution_price")
                                if rp is not None:
                                    rp_val = util.safe_float(rp, -1.0)
                                    if rp_val >= 0:
                                        resolved_outcome = util.resolve_outcome(direction, rp_val)
                                        reason = f"market resolved: {'WIN' if resolved_outcome == 1.0 else 'LOSS'} (resolution={rp_val})"
                                        exit_px = cur

                    # Pre-game exit: sports markets about to start — avoid live volatility.
                    # Only fires for sports/esports (match-like events), not elections etc.
                    end_date = p.get("market_end_date", "")
                    if not reason and end_date and _is_sports_question(p.get("question", "")):
                        mins = _mins_to_resolution(end_date)
                        if mins is not None and 0 <= mins <= 30:
                            reason = f"pre-game exit: match starts in {mins:.0f}min"
                            exit_px = cur

                    # Late-game flip: if losing near resolution, exit + open opposite direction
                    # mechanically — no LLM debate. The market has spoken.
                    flip_reason = None
                    if not reason and end_date and not p.get("resolved"):
                        mins_left = _mins_to_resolution(end_date)
                        loss_pct = util.pnl_pct(cur, entry) or 0
                        # Trigger: within 30 min of resolution AND losing >3%
                        if mins_left is not None and mins_left <= 30 and loss_pct <= -3:
                            flip_reason = f"late flip: {mins_left:.0f}min to resolve, losing {loss_pct:.1f}%"
                            reason = flip_reason
                            exit_px = cur

                    if not reason and trail_high:
                        trail_pct = 0.5 if _spike_trail.get(pid) else WATCHER_TRAIL_PCT
                        if cur <= trail_high * (1 - trail_pct / 100):
                            peak = trail_high
                            reason = f"trailing-stop: price {cur} fell {trail_pct}% below peak {peak}{' (spike)' if trail_pct < 1 else ''}"
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
                        _spike_trail.pop(pid, None)
                        _exited_pick_ids.add(pid)
                        pnl = (exit_px - entry) / entry * 100

                        await loop.run_in_executor(
                            None, lambda pid=pid, r=reason, px=exit_px:
                            store.record_exit(pid, r, px))
                        # If market resolved via API, stamp the official outcome
                        if resolved_outcome is not None:
                            await loop.run_in_executor(
                                None, lambda pid=pid, o=resolved_outcome, pnl_co=pnl:
                                store.mark_resolved(pid, o, pnl_pct=pnl_co))
                        log_w.info("Exit #%d %s %s: %s (pnl %+.1f%%)",
                                   pid, direction, p.get("question", "?")[:50],
                                   reason, pnl)
                        # Post-exit tracking
                        if pnl > 0:
                            global _profit_exit_markets
                            _exited_markets.add(mid)
                            _profit_exit_markets[mid] = direction
                        else:
                            global _loss_exit_markets_global
                            _loss_exit_markets.add(mid)
                            _loss_exit_markets_global[mid] = (direction, time.time())

                        # Late-game flip: open opposite direction mechanically
                        if flip_reason:
                            flip_dir = "BUY_NO" if direction == "BUY_YES" else "BUY_YES"
                            flip_entry = yes_px if flip_dir == "BUY_YES" else no_px
                            await loop.run_in_executor(
                                None, lambda mid=mid, q=p.get("question", ""), fdir=flip_dir,
                                fe=flip_entry, fr=flip_reason, d=direction,
                                yp=yes_px, np=no_px, ed=end_date,
                                pnl_val=pnl, p_vol=p.get("market_volume", 0),
                                p_liq=p.get("market_liquidity", 0):
                                store.record_pick({
                                    "market_id": mid,
                                    "question": q,
                                    "arm": "TEAM",
                                    "edge": "MOMENTUM",
                                    "trigger_src": "STREAM",
                                    "direction": fdir,
                                    "est_probability": round(fe, 4),
                                    "score": 50,
                                    "adjusted_score": 50,
                                    "confidence": 50,
                                    "verdict": "SOFT",
                                    "approved": 1,
                                    "triage_reason": fr,
                                    "thesis": f"Mechanical late-game flip: exited {d} at {pnl_val:.1f}% loss, reversing to {fdir}.",
                                    "model_tags": {"flip": "mechanical"},
                                    "market_yes_price": yp,
                                    "market_no_price": np,
                                    "market_volume": p_vol,
                                    "market_liquidity": p_liq,
                                    "market_end_date": ed,
                                    "taken": 1,
                                }))
                            log_w.info("Flip #%d → %s: %s", pid, flip_dir, p.get("question", "?")[:50])

            # Clean up stale trail entries and resolution cache
            live_ids = {p["id"] for p in picks}
            for pid in list(_trail):
                if pid not in live_ids:
                    _trail.pop(pid, None)
                    _spike_trail.pop(pid, None)
            # Prune resolution check cache — keep only entries from last 24h
            cutoff = time.time() - 86400
            for mid in list(_resolution_checked):
                if _resolution_checked[mid] < cutoff:
                    del _resolution_checked[mid]

            # Push live pick data to SSE clients (real-time UI)
            try:
                from altavela.app.dashboard import push_live_picks, _live_queues
                if _live_queues:
                    result = []
                    for p in picks:
                        if p["id"] in _exited_pick_ids:
                            continue
                        mid = p.get("market_id", "")
                        yes_px, no_px = prices.get(mid, (None, None))
                        direction = p.get("direction", "")
                        entry = util.entry_price(p, direction)
                        cur = yes_px if direction == "BUY_YES" else no_px
                        result.append({
                            "id": p["id"],
                            "ts": p.get("ts"),
                            "question": (p.get("question") or "")[:100],
                            "direction": direction,
                            "score": p.get("adjusted_score") or p.get("score"),
                            "entry_price": entry,
                            "current_price": cur,
                            "pnl_pct": util.pnl_pct(cur, entry) if cur and entry else None,
                            "resolved": bool(p.get("resolved")),
                            "outcome": p.get("outcome"),
                        })
                    push_live_picks({"items": result, "total": len(result)})
            except Exception:
                pass
        except Exception as exc:
            log_w.error("watcher error: %s", exc)
        interval = 2 if stream_prices else WATCHER_INTERVAL_S
        await asyncio.sleep(interval)   # 2s with streaming, configured otherwise


async def _desk() -> None:
    """One-shot run: fetch markets, scout, debate, write to ledger."""
    import time
    from altavela.config import REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT
    from altavela.ingest.polymarket import fetch_markets, quality_filter
    from altavela.desk.scout import run_scout
    from altavela.ledger import store

    log = logging.getLogger("altavela.desk")

    log.info("Fetching active prediction markets…")
    markets = fetch_markets(limit=100, min_volume=10000)
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
    fresh_markets, skipped_cooldown = util.apply_cooldown_filter(
        markets, recent, REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT)

    # Block in-match entries — live sports/esports gap through stops
    filtered = []
    skipped_in_match = 0
    for m in fresh_markets:
        mins = _mins_to_resolution(m.get("end_date", ""))
        if mins is not None and mins <= 120:
            skipped_in_match += 1
            continue
        filtered.append(m)
    fresh_markets = filtered

    if skipped_cooldown or skipped_in_match:
        log.info("Cooldown: %d skipped, in-match: %d blocked, %d passed",
                 skipped_cooldown, skipped_in_match, len(fresh_markets))

    if not fresh_markets:
        log.info("No fresh markets after cooldown filter")
        return

    log.info("Scout scanning %d markets", len(fresh_markets))
    store.add_run("DESK")  # Record attempt even if scout finds nothing
    result = run_scout(fresh_markets)
    picks = result.get("picks", [])

    if not picks:
        log.info("Scout found nothing worth debating (%d skips)", len(result.get("skips", [])))
        return

    log.info("Scout picked %d markets for debate", len(picks))

    # Simple sequential debate (no streaming — headless mode)
    from altavela.ingest.evidence import gather_evidence

    for pick in picks:
        try:
            mid = pick["market_id"]
            market = next((m for m in fresh_markets if m["id"] == mid), {})
            if not market:
                continue

            # Unified entry gate: post-profit, post-loss, underwater
            market_prices = market.get("prices", [0.5, 0.5])
            allow, block_reason = _entry_gate(mid, pick["direction"], market_prices, store.live_picks)
            if not allow:
                log.info("Skipping '%s': %s", pick["question"][:60], block_reason)
                continue

            # Gather evidence
            loop = asyncio.get_running_loop()
            evidence = await loop.run_in_executor(
                None, gather_evidence, pick["question"], market)

            # Add mathematical signals to evidence
            try:
                from altavela.desk.math import compute_signals
                math_signals = compute_signals(
                    mid, market.get("prices", [0.5, 0.5])[0] if market.get("prices") else 0.5,
                    market.get("prices", [0.5, 0.5])[1] if len(market.get("prices", [])) > 1 else 0.5,
                    market.get("volume", 0), market.get("end_date", ""),
                    pick.get("direction", ""))
                evidence.extend(math_signals)
            except Exception as exc:
                log.debug("Math signals failed: %s", exc)

            # Add profit-exit context for the researcher
            reversion_note = ""
            if mid in _profit_exit_markets:
                profit_dir = _profit_exit_markets[mid]
                reversion_note = f"[NOTE] This market recently had a profitable {profit_dir} exit. Consider mean reversion carefully — the easy move may already have happened."
                evidence.append(reversion_note)

                # Reversion gate: only approve reverse bets that make sense
                if pick["direction"] != profit_dir:
                    from altavela.desk.team import reversion_gate
                    gate = await loop.run_in_executor(
                        None, lambda: reversion_gate(
                            pick["question"], profit_dir, pick["direction"], evidence))
                    if not gate.get("approve_reversion", False):
                        log.info("Reversion REJECTED for '%s': %s", pick["question"][:60], gate.get("reason", ""))
                        continue
                    log.info("Reversion APPROVED for '%s': %s", pick["question"][:60], gate.get("reason", ""))

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
                    pid = ev.get("pick_id")
                    if pid:
                        log.info("  Booked #%d", pid)
                    else:
                        log.info("  Skipped")
        except Exception as exc:
            log.warning("Pick failed for '%s': %s", pick["question"][:60], exc)

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
