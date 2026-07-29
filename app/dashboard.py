"""Altavela dashboard — FastAPI server for the prediction-market research desk."""

import json as _json
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from altavela.ledger import store
from altavela.llm import set_token_sink

set_token_sink(store.token_sink)

log = logging.getLogger("altavela.dashboard")

_STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Altavela")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Basic Auth middleware — applied to /api/* only, static files are public
    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        from altavela.config import ADMIN_USERNAME, ADMIN_PASSWORD
        if not ADMIN_PASSWORD:
            return await call_next(request)
        import base64
        auth = request.headers.get("authorization", "")
        expected = base64.b64encode(f"{ADMIN_USERNAME}:{ADMIN_PASSWORD}".encode()).decode()
        if not auth or auth != f"Basic {expected}":
            from fastapi.responses import Response
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Altavela"'},
            )
        return await call_next(request)

    @app.get("/api/stats")
    async def api_stats():
        return store.stats()

    @app.get("/api/picks")
    async def api_picks(limit: int = 20, offset: int = 0):
        from altavela.ingest.polymarket import live_prices as pm_prices

        picks = store.live_picks()
        total = len(picks)
        picks = picks[offset:offset + limit]
        mids = list({p["market_id"] for p in picks if p.get("market_id")})
        prices = pm_prices(mids) if mids else {}

        result = []
        for p in picks:
            mid = p.get("market_id", "")
            yes_px, no_px = prices.get(mid, (None, None))
            direction = p.get("direction", "")
            entry = p.get("market_yes_price") if direction == "BUY_YES" else p.get("market_no_price")
            cur = yes_px if direction == "BUY_YES" else no_px
            pnl_pct = None
            if entry and cur and entry > 0:
                pnl_pct = round((cur - entry) / entry * 100, 1)

            result.append({
                "id": p["id"],
                "ts": p.get("ts"),
                "question": (p.get("question") or "")[:100],
                "direction": direction,
                "score": p.get("adjusted_score") or p.get("score"),
                "entry_price": entry,
                "current_price": cur,
                "pnl_pct": pnl_pct,
                "resolved": bool(p.get("resolved")),
                "outcome": p.get("outcome"),
            })
        return {"items": result, "total": total}

    @app.get("/api/timelines")
    async def api_timelines(exited_only: str = "1", limit: int = 20, offset: int = 0):
        """All picks with outcomes — for the track record."""
        rows, total = store.all_picks(limit=limit, offset=offset, exited_only=exited_only != "0")
        return {"items": rows, "total": total}
        return rows

    @app.get("/api/pick/{pick_id}")
    async def api_pick(pick_id: int):
        """Full pick detail — for the drill-down panel."""
        row = store.get_pick(pick_id)
        if not row:
            return {"error": "not found"}
        return row

    @app.get("/api/rate-limits")
    async def api_rate_limits():
        from altavela.llm import rate_limit_stats
        return rate_limit_stats()

    @app.get("/api/tokens")
    async def api_tokens():
        """Token usage summary."""
        result = store.token_summary()
        return result

    @app.get("/api/find-markets")
    async def sse_find_markets(request: Request):
        """SSE endpoint — signal-driven pipeline, streams events live."""
        import asyncio

        async def stream():
            from altavela.ingest.polymarket import fetch_markets, quality_filter
            from altavela.ingest.evidence import gather_evidence
            from altavela.desk.signal import sanity_check
            from altavela.desk.math import compute_signals
            from altavela.config import REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT, MAX_PICKS_PER_WINDOW

            loop = asyncio.get_running_loop()

            def _ev(t, **kw):
                d = {"type": t, **kw}
                return f"data: {_json.dumps(d, default=str)}\n\n"

            yield _ev("status", msg="Fetching active prediction markets…")
            markets = await loop.run_in_executor(None, lambda: fetch_markets(limit=200, min_volume=10000))
            if not markets:
                yield _ev("done", msg="No active markets found")
                return

            markets = await loop.run_in_executor(None, quality_filter, markets)
            if not markets:
                yield _ev("done", msg="No markets passed quality filter")
                return

            recent = await loop.run_in_executor(None, lambda: store.markets_debated_since(REPICK_COOLDOWN_HOURS))
            fresh_markets = []
            for m in markets:
                mid = m.get("id", "")
                if mid in recent:
                    prev = recent[mid]
                    prices = m.get("prices", [0.5, 0.5])
                    cur_yes = prices[0] if len(prices) > 0 else 0.5
                    prev_yes = prev.get("yes_price") if prev.get("yes_price") is not None else 0.5
                    if prev_yes > 0 and abs(cur_yes - prev_yes) / prev_yes * 100 < REPICK_MIN_PRICE_MOVE_PCT:
                        continue
                fresh_markets.append(m)

            yield _ev("status", msg=f"Signal scanning {len(fresh_markets)} markets…")

            # Score markets by math signals
            scored = []
            for m in fresh_markets:
                prices = m.get("prices", [0.5, 0.5])
                yes_px = prices[0] if len(prices) > 0 else 0.5
                direction = "BUY_YES" if yes_px >= 0.5 else "BUY_NO"
                signals = compute_signals(m["id"], yes_px, prices[1] if len(prices) > 1 else 0.5,
                                          m.get("volume", 0), m.get("end_date", ""), direction)
                velocity_score = sum(1 for s in signals if "VELOCITY" in s and "flat" not in s) * 3
                uncertainty = abs(yes_px - 0.5)
                uncertainty_score = (0.25 - uncertainty) * 20
                score = velocity_score + max(0, uncertainty_score)
                if score > 0:
                    scored.append((m, direction, signals, score))

            scored.sort(key=lambda x: x[3], reverse=True)
            picks = scored[:MAX_PICKS_PER_WINDOW]

            if not picks:
                yield _ev("done", msg="No signal-worthy markets found")
                return

            yield _ev("status", msg=f"Signal picked {len(picks)} markets for sanity check")

            for market, direction, math_signals, score in picks:
                if await request.is_disconnected():
                    yield _ev("done", msg="Client disconnected")
                    return

                mid = market["id"]
                yield _ev("signal_pick", question=market["question"], direction=direction,
                          score=score, signals=len(math_signals))

                evidence = await loop.run_in_executor(
                    None, gather_evidence, market["question"], market)
                evidence.extend(math_signals)
                if evidence:
                    yield _ev("evidence", msg=f"{len(evidence)} items for '{market['question'][:60]}'")

                entry_px = market["prices"][0] if direction == "BUY_YES" else market["prices"][1] if len(market.get("prices", [])) > 1 else 0.5
                check = await loop.run_in_executor(
                    None, lambda: sanity_check(
                        market["question"], direction, entry_px, math_signals, evidence))

                yield _ev("sanity", question=market["question"], direction=direction,
                          approved=check.get("approve", True), reason=check.get("reason", ""))

                if not check.get("approve", True):
                    yield _ev("status", msg=f"Sanity REJECTED: {market['question'][:60]}")
                    continue

                # Book directly
                prices = market.get("prices", [0.5, 0.5])
                pick_id = await loop.run_in_executor(
                    None, lambda: store.record_pick({
                        "market_id": market["id"],
                        "question": market["question"],
                        "arm": "TEAM",
                        "edge": "MATH",
                        "trigger_src": "STREAM",
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
                yield _ev("booked", pick_id=pick_id, question=market["question"],
                          direction=direction, score=score)

            await loop.run_in_executor(None, lambda: store.add_run("STREAM"))
            yield _ev("done", msg=f"{len(picks)} sanity-checked")

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    if _STATIC.exists() and (_STATIC / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def index():
            return "<html><body style='background:#0a0a0a;color:#e4e4e7;font-family:system-ui;padding:2rem'><h1>Altavela</h1><p>API running. Build the UI with <code>cd altavela/ui && pnpm build</code></p></body></html>"

    return app
