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
    async def api_picks():
        from altavela.ingest.polymarket import live_prices as pm_prices

        picks = store.live_picks()
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
                "question": (p.get("question") or "")[:100],
                "direction": direction,
                "score": p.get("adjusted_score") or p.get("score"),
                "entry_price": entry,
                "current_price": cur,
                "pnl_pct": pnl_pct,
                "resolved": bool(p.get("resolved")),
                "outcome": p.get("outcome"),
            })
        return result

    @app.get("/api/timelines")
    async def api_timelines():
        """All picks with outcomes — for the track record."""
        rows = store.all_picks(limit=50)
        return rows

    @app.get("/api/tokens")
    async def api_tokens():
        """Token usage summary."""
        result = store.token_summary()
        return result

    @app.get("/api/find-markets")
    async def sse_find_markets(request: Request):
        """SSE endpoint — runs the full pipeline and streams events live."""
        import asyncio

        async def stream():
            from altavela.ingest.polymarket import fetch_markets, quality_filter
            from altavela.ingest.evidence import gather_evidence
            from altavela.desk.scout import run_scout as scout_run
            from altavela.desk.debate import deliberate
            from altavela.config import REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT

            loop = asyncio.get_running_loop()

            def _ev(t, **kw):
                d = {"type": t, **kw}
                return f"data: {_json.dumps(d, default=str)}\n\n"

            yield _ev("status", msg="Fetching active prediction markets…")
            markets = await loop.run_in_executor(None, lambda: fetch_markets(limit=50, min_volume=5000))
            if not markets:
                yield _ev("done", msg="No active markets found")
                return

            markets = await loop.run_in_executor(None, quality_filter, markets)
            if not markets:
                yield _ev("done", msg="No markets passed quality filter")
                return

            # Cooldown filter
            recent = await loop.run_in_executor(None, lambda: store.markets_debated_since(REPICK_COOLDOWN_HOURS))
            fresh_markets = []
            for m in markets:
                mid = m.get("id", "")
                if mid in recent:
                    prev = recent[mid]
                    prices = m.get("prices", [0.5, 0.5])
                    cur_yes = prices[0] if len(prices) > 0 else 0.5
                    prev_yes = prev.get("yes_price") or 0.5
                    if prev_yes > 0 and abs(cur_yes - prev_yes) / prev_yes * 100 < REPICK_MIN_PRICE_MOVE_PCT:
                        continue
                fresh_markets.append(m)

            yield _ev("status", msg=f"Scout scanning {len(fresh_markets)} markets…")
            result = await loop.run_in_executor(None, scout_run, fresh_markets)
            picks = result.get("picks", [])

            if not picks:
                yield _ev("done", msg=f"No picks — {len(result.get('skips',[]))} skipped")
                return

            for pick in picks:
                mid = pick["market_id"]
                market = next((m for m in fresh_markets if m["id"] == mid), {})
                if not market:
                    continue

                yield _ev("debate_start", question=pick["question"], edge_hint=pick.get("edge_hint"))

                evidence = await loop.run_in_executor(
                    None, gather_evidence, pick["question"], market)
                if evidence:
                    yield _ev("evidence", msg=f"{len(evidence)} articles found for '{pick['question'][:60]}'")

                yield _ev("scout_pick", question=pick["question"], direction=pick["direction"],
                          edge_hint=pick.get("edge_hint"), reason=pick.get("reason"))

                async for ev_data in deliberate(market, pick, evidence, "STREAM", None):
                    t = ev_data.get("type", "")
                    if t != "_result":
                        yield _ev(t, **{k: v for k, v in ev_data.items() if k != "type"})

            await loop.run_in_executor(None, lambda: store.add_run("STREAM"))
            yield _ev("done", msg=f"{len(picks)} debated")

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
