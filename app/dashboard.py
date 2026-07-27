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
        picks = store.live_picks()
        return [
            {
                "id": p["id"],
                "question": p.get("question", ""),
                "direction": p.get("direction", ""),
                "score": p.get("adjusted_score") or p.get("score"),
                "market_yes_price": p.get("market_yes_price"),
                "resolved": bool(p.get("resolved")),
                "outcome": p.get("outcome"),
            }
            for p in picks
        ]

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
            <th>ID</th><th>Question</th><th>Direction</th><th>Score</th><th>Price</th>
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
                const tr = document.createElement('tr');
                tr.innerHTML = '<td>#'+p.id+'</td><td>'+p.question.slice(0,80)+'</td>'+
                  '<td>'+p.direction+'</td><td>'+p.score+'</td><td>'+p.market_yes_price+'</td>';
                tbody.appendChild(tr);
              });
            }
            load();
          </script>
        </body></html>"""

    return app
