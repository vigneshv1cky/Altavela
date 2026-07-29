# Altavela

Multi-agent prediction-market research engine. LLMs scan Polymarket, debate true probabilities, and book directional calls. Research mode — no on-chain execution.

## How it works

1. **Fetch** — Polymarket Gamma API, 50 markets, ≥$10K volume
2. **Filter** — drop non-binary + YES <5% or >95%
3. **Cooldown** — skip markets debated within 6h unless price moved >5%
4. **Scout** — LLM picks up to 5 markets with highest edge
5. **Evidence** — DuckDuckGo web search + Polymarket metadata. Skip if no evidence found.
6. **Debate** — researcher → critic → rebuttal → judge pipeline
7. **Book** — write to SQLite ledger with full debate transcript
8. **Watch** — trailing stop + stop loss + stale exit every 60s

## Watcher

Three exit triggers checked every 60s per open position:

| Trigger | Config | Default |
|---------|--------|---------|
| Trailing stop | `TAKE_PROFIT_PCT` / `TRAIL_PCT` | +10% activate, 5% trail |
| Stop loss | `STOP_PCT` | 5% |
| Stale exit | `STALE_HOURS` / `STALE_MOVE_PCT` | 4h, <1% movement |
| Market resolution | auto | price ≤0.001 or ≥0.999 → WIN/LOSS |
| Pre-game exit | auto | 30min before sports kickoff |

## Quick start

```bash
cp .env.example .env    # add DEEPSEEK_API_KEY
python -m altavela.main dashboard
```

Open `http://localhost:8001`.

## Commands

```bash
python -m altavela.main dashboard   # web dashboard + autorun + grader + watcher
python -m altavela.main desk        # one-shot headless run
python -m altavela.main status      # ledger stats
```

## Config

All via `.env`. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_PROVIDER` | `deepseek` | LLM backend (deepseek/kimi) |
| `DEEPSEEK_API_KEY` | — | API key |
| `AUTORUN_INTERVAL_HOURS` | `0.25` | How often autorun fires |
| `MAX_PICKS_PER_WINDOW` | `5` | Scout pick limit per run |
| `REPICK_COOLDOWN_HOURS` | `6` | Don't re-debate same market |
| `REPICK_MIN_PRICE_MOVE_PCT` | `5` | Unless price moved this much |
| `WATCHER_TAKE_PROFIT_PCT` | `10` | Trailing stop activation |
| `WATCHER_TRAIL_PCT` | `5` | Trail margin below peak |
| `WATCHER_STOP_PCT` | `5` | Stop loss |
| `WATCHER_STALE_HOURS` | `4` | Stale exit timeout |
| `WATCHER_INTERVAL_S` | `60` | Watcher check frequency |
| `DASHBOARD_PORT` | `8001` | Web UI port |

## Architecture

```
altavela/
├── main.py              # Entry point, autorun, watcher, grader loops
├── config.py            # All env vars and settings
├── llm.py               # LLM layer (DeepSeek/Kimi), rate limits, validation
├── ingest/
│   ├── polymarket.py    # Gamma API client, quality filter
│   └── evidence.py      # DuckDuckGo web search, evidence pipeline
├── desk/
│   ├── scout.py         # LLM scans markets for edge
│   ├── debate.py        # Researcher → critic → rebuttal → judge pipeline
│   └── team.py          # LLM prompts for each role
├── ledger/
│   ├── store.py         # SQLite/WAL store for picks, runs, tokens
│   └── grader.py        # Checks Polymarket for resolved outcomes
├── app/
│   ├── dashboard.py     # FastAPI REST + SSE endpoints
│   └── static/          # Built React UI
└── ui/                  # React 19 + Vite + Tailwind v4 + shadcn/ui
```

## Deploy

```bash
# Backend
git push && gcloud compute ssh altavela --command \
  'cd /opt/altavela && sudo git pull && sudo systemctl restart altavela'

# Frontend
cd ui && pnpm build && pnpm deploy
```

## Dashboard UI

- **Live** — open positions, grouped by date, sorted by entry time, green/red badges
- **History** — exited-only with P&L card (total + median), sorted by exit time
- **Usage** — token accounting by role/model
- Click any card for full debate transcript drill-down
