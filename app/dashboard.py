"""Altavela dashboard — FastAPI server for the prediction-market research desk."""

import json as _json
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from altavela.ledger import store

log = logging.getLogger("altavela.dashboard")


def create_app() -> FastAPI:
    app = FastAPI(title="Altavela")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Basic Auth middleware
    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
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
                pnl_pct = round((cur - entry) / entry * 100, 1) if direction == "BUY_YES" else round((entry - cur) / entry * 100, 1)

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

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return """
        <!doctype html>
        <html><head><title>Altavela</title>
        <meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <style>
          body { font-family: system-ui; margin: 2rem; background: #0a0a0a; color: #e4e4e7; }
          h1 { font-size: 1.25rem; }
          .stats { display: flex; gap: 2rem; margin: 1rem 0; }
          .stat { font-size: 2rem; font-weight: bold; }
          .label { font-size: 0.75rem; color: #71717a; }
          table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
          th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #27272a; }
        </style></head>
        <body>
          <h1>Altavela — Prediction Markets</h1>
          <div class="stats" id="stats"></div>
          <table id="picks"><thead><tr>
            <th>ID</th><th>Question</th><th>Direction</th><th>Entry</th><th>Current</th><th>P&L</th>
          </tr></thead><tbody></tbody></table>
          <script>
            async function load() {
              const s = await fetch('/api/stats').then(r=>r.json());
              document.getElementById('stats').innerHTML =
                '<div><div class="stat">'+s.total_picks+'</div><div class="label">Picks</div></div>'+
                '<div><div class="stat">'+s.resolved+'</div><div class="label">Resolved</div></div>'+
                '<div><div class="stat">'+(s.win_rate!=null?s.win_rate+'%':'—')+'</div><div class="label">Win Rate</div></div>';
              const picks = await fetch('/api/picks').then(r=>r.json());
              const tbody = document.querySelector('#picks tbody');
              picks.forEach(p => {
                const pnl = p.pnl_pct != null ? (p.pnl_pct >= 0 ? '+' : '') + p.pnl_pct + '%' : '—';
                const color = p.pnl_pct != null ? (p.pnl_pct >= 0 ? 'color:#4ade80' : 'color:#f87171') : '';
                const tr = document.createElement('tr');
                tr.innerHTML = '<td>#'+p.id+'</td><td>'+(p.question||'').slice(0,80)+'</td>'+
                  '<td>'+p.direction+'</td><td>'+(p.entry_price||'—')+'</td><td>'+(p.current_price||'—')+'</td>'+
                  '<td style="'+color+'">'+pnl+'</td>';
                tbody.appendChild(tr);
              });
            }
            load();
            setInterval(load, 60000);
          </script>
        </body></html>"""

    return app
