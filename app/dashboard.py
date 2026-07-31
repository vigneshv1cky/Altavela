"""Altavela dashboard — FastAPI server for the prediction-market research desk."""

import asyncio
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
import altavela.util as util

set_token_sink(store.token_sink)

log = logging.getLogger("altavela.dashboard")

_STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

# SSE push for live pick updates
_live_queues: list[asyncio.Queue] = []


def push_live_picks(data: dict) -> None:
    """Push live pick data to all connected SSE clients (called from watcher)."""
    for q in list(_live_queues):
        try:
            q.put_nowait(data)
        except asyncio.QueueFull:
            pass


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
        from altavela.ingest.stream import get_prices as pm_prices

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
            entry = util.entry_price(p, direction)
            cur = yes_px if direction == "BUY_YES" else no_px
            pnl_pct = util.pnl_pct(cur, entry) if cur and entry else None

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
            markets = await loop.run_in_executor(None, lambda: fetch_markets(limit=100, min_volume=10000))
            if not markets:
                yield _ev("done", msg="No active markets found")
                return

            markets = await loop.run_in_executor(None, quality_filter, markets)
            if not markets:
                yield _ev("done", msg="No markets passed quality filter")
                return

            # Cooldown filter
            recent = await loop.run_in_executor(None, lambda: store.markets_debated_since(REPICK_COOLDOWN_HOURS))
            fresh_markets, _ = util.apply_cooldown_filter(markets, recent, REPICK_COOLDOWN_HOURS, REPICK_MIN_PRICE_MOVE_PCT)

            yield _ev("status", msg=f"Scout scanning {len(fresh_markets)} markets…")
            result = await loop.run_in_executor(None, scout_run, fresh_markets)
            picks = result.get("picks", [])

            if not picks:
                yield _ev("done", msg=f"No picks — {len(result.get('skips',[]))} skipped")
                return

            for pick in picks:
                if await request.is_disconnected():
                    yield _ev("done", msg="Client disconnected")
                    return
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

    @app.get("/api/live-picks")
    async def sse_live_picks(request: Request):
        """SSE endpoint — pushes live position data with real-time P&L."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        _live_queues.append(queue)
        try:
            async def stream():
                while not await request.is_disconnected():
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {_json.dumps(data, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            return StreamingResponse(stream(), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})
        finally:
            _live_queues.remove(queue)

    if _STATIC.exists() and (_STATIC / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def index():
            return "<html><body style='background:#0a0a0a;color:#e4e4e7;font-family:system-ui;padding:2rem'><h1>Altavela</h1><p>API running. Build the UI with <code>cd altavela/ui && pnpm build</code></p></body></html>"

    return app
