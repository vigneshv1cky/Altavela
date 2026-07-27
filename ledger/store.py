"""Altavela ledger — SQLite/WAL store for prediction-market picks, runs, and tokens."""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from altavela.config import DATA_DIR

log = logging.getLogger("altavela.store")

_DB = DATA_DIR / "ledger.db"
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    market_id       TEXT NOT NULL,
    question        TEXT NOT NULL,
    arm             TEXT NOT NULL,       -- TEAM | LONER
    edge            TEXT,                -- MISPRICING | INFORMATION | CALENDAR | MOMENTUM
    trigger_src     TEXT NOT NULL,       -- STREAM | AUTO
    -- decision
    direction       TEXT NOT NULL,       -- BUY_YES | BUY_NO
    est_probability REAL NOT NULL,       -- agent's estimated true probability (0-1)
    score           REAL NOT NULL,       -- pre-debate confidence
    adjusted_score  REAL,                -- post-debate
    confidence      REAL NOT NULL,
    verdict         TEXT,                -- STRONG | SOFT | PASS
    approved        INTEGER NOT NULL DEFAULT 0,
    -- context
    triage_reason   TEXT,
    thesis          TEXT,
    debate          TEXT,                -- JSON transcript
    briefs          TEXT,                -- JSON
    model_tags      TEXT,
    -- market snapshot at decision time
    market_yes_price REAL,               -- current YES price on polymarket
    market_no_price  REAL,               -- current NO price
    market_volume    REAL,
    market_liquidity REAL,
    market_end_date  TEXT,
    -- outcomes
    resolved        INTEGER NOT NULL DEFAULT 0,     -- 1 = resolved, 0 = not yet
    outcome         REAL,                           -- 1.0 = correct, 0.0 = wrong, NULL = unresolved
    resolved_at     TEXT,
    -- P&L tracking (binary)
    pnl_return_pct  REAL,                -- mark-to-market return (current price - entry price)
    pnl_usd         REAL,               -- dollar P&L (paper)
    graded_at       TEXT,
    -- position lifecycle
    taken           INTEGER NOT NULL DEFAULT 0,
    exit_ts         TEXT,
    exit_reason     TEXT,
    exit_price      REAL,

    -- paper trading (Polymarket CLOB)
    broker_order_id  TEXT,
    broker_status    TEXT,
    broker_qty       REAL,
    broker_fill_price REAL,
    broker_fill_ts   TEXT,
    position_size    REAL               -- USD size at entry
);
CREATE INDEX IF NOT EXISTS idx_picks_ts ON picks (ts);
CREATE INDEX IF NOT EXISTS idx_picks_market ON picks (market_id);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    role         TEXT NOT NULL,
    model        TEXT NOT NULL,
    input_tok    INTEGER NOT NULL,
    output_tok   INTEGER NOT NULL,
    decision_id  TEXT,
    source       TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def init() -> None:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _connect() as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_JSON_FIELDS = ("debate", "briefs", "model_tags")


def _check_cols(keys) -> None:
    for k in keys:
        if not isinstance(k, str) or not k.isidentifier():
            raise ValueError(f"invalid column name: {k!r}")


# ---------------------------------------------------------------------------
# Picks
# ---------------------------------------------------------------------------

def record_pick(row: dict[str, Any]) -> int:
    row = dict(row)
    row.setdefault("ts", _now())
    for field in _JSON_FIELDS:
        if field in row and not isinstance(row[field], (str, type(None))):
            row[field] = json.dumps(row[field])
    _check_cols(row)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with _lock, _connect() as conn:
        cur = conn.execute(f"INSERT INTO picks ({cols}) VALUES ({marks})", list(row.values()))
        return int(cur.lastrowid or 0)


def update_pick(pick_id: int, **fields: Any) -> None:
    for field in _JSON_FIELDS:
        if field in fields and not isinstance(fields[field], (str, type(None))):
            fields[field] = json.dumps(fields[field])
    _check_cols(fields)
    sets = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _connect() as conn:
        conn.execute(f"UPDATE picks SET {sets} WHERE id = ?", (*fields.values(), pick_id))


def due_for_grading(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM picks WHERE graded_at IS NULL AND resolved=0 ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def live_picks() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM picks WHERE taken=1 AND resolved=0 AND exit_ts IS NULL"
            " ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_resolved(pick_id: int, outcome: float) -> None:
    with _lock, _connect() as conn:
        conn.execute(
            "UPDATE picks SET resolved=1, outcome=?, resolved_at=? WHERE id=?",
            (round(float(outcome), 4), _now(), int(pick_id)),
        )


def record_exit(pick_id: int, reason: str, exit_price: float | None = None) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE picks SET exit_ts=?, exit_reason=?, exit_price=? WHERE id=? AND exit_ts IS NULL",
            (_now(), str(reason)[:300], exit_price, int(pick_id)),
        )
        return cur.rowcount > 0


def last_run_time(kind: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT ts FROM runs WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
    return row["ts"] if row else None


def add_run(kind: str) -> int:
    with _lock, _connect() as conn:
        cur = conn.execute("INSERT INTO runs (ts, kind) VALUES (?, ?)", (_now(), kind))
        return int(cur.lastrowid or 0)


def stats() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) as n FROM picks WHERE arm='TEAM'").fetchone()
        resolved = conn.execute(
            "SELECT COUNT(*) as n FROM picks WHERE arm='TEAM' AND resolved=1"
        ).fetchone()
        wins = conn.execute(
            "SELECT COUNT(*) as n FROM picks WHERE arm='TEAM' AND resolved=1 AND outcome=1.0"
        ).fetchone()
    return {
        "total_picks": total["n"],
        "resolved": resolved["n"],
        "wins": wins["n"],
        "win_rate": round(wins["n"] / resolved["n"] * 100, 1) if resolved["n"] > 0 else None,
    }


# ---------------------------------------------------------------------------
# Token sink
# ---------------------------------------------------------------------------

def token_sink(role: str, model: str, input_tok: int, output_tok: int,
               decision_id: str | None = None, source: str | None = None) -> None:
    try:
        with _lock, _connect() as conn:
            conn.execute(
                "INSERT INTO token_usage (ts, role, model, input_tok, output_tok, decision_id, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), role, model, input_tok, output_tok, decision_id, source),
            )
    except Exception:
        log.debug("token_sink failed", exc_info=True)


# Init on import
init()
