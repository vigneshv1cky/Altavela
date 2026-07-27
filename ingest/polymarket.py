"""Polymarket ingest — fetch active prediction markets from the Gamma API.

Endpoints (free, no auth):
  GET /markets       — list markets (filter by closed=false, liquidity, volume)
  GET /events        — list events (grouped)
  GET /markets/{id}  — single market details

Each market becomes a candidate for the scout: question, outcomes, prices, volume,
liquidity, close date, resolution source.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from altavela.config import POLYMARKET_BASE

log = logging.getLogger("altavela.polymarket")

_CACHE: dict[str, Any] = {}
_CACHE_TS: float = 0.0
_CACHE_TTL = 120  # 2 min


def _get(path: str) -> dict | list:
    url = f"{POLYMARKET_BASE}{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "altavela/0.1"})
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(2.0 * attempt)
                continue
            log.warning("Polymarket fetch failed (%s): %s", path, exc)
            return []
        except Exception as exc:
            log.warning("Polymarket fetch failed (%s): %s", path, exc)
            return []
    return []


def _parse_json_field(m: dict, key: str) -> list:
    """Polymarket returns some fields as JSON-encoded strings like '[\"Yes\", \"No\"]'."""
    val = m.get(key)
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip():
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


def fetch_markets(
    limit: int = 100,
    min_volume: float = 5000.0,
    min_liquidity: float = 1000.0,
) -> list[dict]:
    """Fetch active markets with meaningful volume and liquidity.

    Returns list of market dicts with:
      id, question, description, outcomes (list), outcomePrices (list),
      volume, liquidity, closed, endDate, category, events (list of tags)
    """
    params = (
        f"?closed=false&limit={limit}"
        f"&volume_num_min={min_volume}"
        f"&liquidity_num_min={min_liquidity}"
        f"&order=volume&ascending=false"
    )
    data = _get(f"/markets{params}")
    if isinstance(data, dict):
        data = data.get("data", data.get("markets", []))
    if not isinstance(data, list):
        return []

    # Flatten to standard fields
    out = []
    for m in data:
        if not isinstance(m, dict):
            continue

        # Polymarket Gamma API returns these as JSON-encoded strings
        outcomes_raw = _parse_json_field(m, "outcomes")
        prices_raw = _parse_json_field(m, "outcomePrices")
        clob_ids = _parse_json_field(m, "clobTokenIds")

        prices = []
        for p in prices_raw:
            try:
                prices.append(round(float(p), 4))
            except (ValueError, TypeError):
                prices.append(0.0)

        # Collapse to binary: if there are >2 outcomes, use the first two (Yes/No)
        # and note the rest as alternative outcomes.
        if len(outcomes_raw) > 2:
            outcomes_raw = outcomes_raw[:2]
            prices = prices[:2]

        tags = []
        events = m.get("events")
        if isinstance(events, list):
            for t in events:
                if isinstance(t, dict):
                    tags.append(t.get("title") or t.get("slug") or "")
        elif isinstance(events, str):
            try:
                events_parsed = json.loads(events)
                if isinstance(events_parsed, list):
                    for t in events_parsed:
                        if isinstance(t, dict):
                            tags.append(t.get("title") or t.get("slug") or "")
            except json.JSONDecodeError:
                pass
        tags = [t for t in tags if t]

        vol = float(m.get("volume", 0) or 0)
        liq = float(m.get("liquidity", 0) or 0)
        end_ts = m.get("endDate") or m.get("end_date") or m.get("closeTime") or ""

        out.append({
            "id": str(m.get("id", "")),
            "question": (m.get("question") or m.get("title", ""))[:250],
            "description": (m.get("description") or "")[:400],
            "outcomes": [str(o) for o in outcomes_raw],
            "prices": prices,
            "volume": round(vol, 2),
            "liquidity": round(liq, 2),
            "closed": bool(m.get("closed", False)),
            "end_date": str(end_ts)[:19],
            "category": str(m.get("category") or m.get("tags", []) if isinstance(m.get("tags"), str) else ""),
            "tags": tags[:5],
        })

    log.info("Polymarket: %d active markets (vol>%s liq>%s)", len(out), min_volume, min_liquidity)
    return out


def quality_filter(markets: list[dict]) -> list[dict]:
    """Filter out low-quality markets: near-expiry, extreme prices, non-binary.
    Returns filtered list and logs what was dropped."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=1)
    filtered = []
    dropped_expiry = dropped_binary = dropped_price = 0

    for m in markets:
        # Skip markets closing within 1 hour — noise, not edge
        end_str = m.get("end_date", "")
        if end_str:
            try:
                end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end <= cutoff:
                    dropped_expiry += 1
                    continue
            except (ValueError, TypeError):
                pass

        # Skip non-binary markets (>2 outcomes)
        outcomes = m.get("outcomes", [])
        if len(outcomes) != 2:
            dropped_binary += 1
            continue

        # Skip extreme prices — no edge left (<$0.02 or >$0.98)
        prices = m.get("prices", [0.5, 0.5])
        yes_px = prices[0] if len(prices) > 0 else 0.5
        no_px = prices[1] if len(prices) > 1 else 0.5
        if yes_px < 0.02 or yes_px > 0.98 or no_px < 0.02 or no_px > 0.98:
            dropped_price += 1
            continue

        filtered.append(m)

    if dropped_expiry or dropped_binary or dropped_price:
        log.info("Quality filter: %d near-expiry, %d non-binary, %d extreme-price — %d passed",
                 dropped_expiry, dropped_binary, dropped_price, len(filtered))
    return filtered
    """Fetch full details for one market, including resolution history."""
    data = _get(f"/markets/{market_id}")
    if isinstance(data, dict) and data.get("id"):
        return data
    return None


def market_detail(market_id: str) -> dict | None:
    """Fetch full details for one market, including resolution history."""
    data = _get(f"/markets/{market_id}")
    if isinstance(data, dict) and data.get("id"):
        return data
    return None


def live_prices(market_ids: list[str]) -> dict[str, tuple[float, float]]:
    """Get current (YES, NO) prices for one or more markets. Returns {id: (yes_px, no_px)}.
    Uses the single-market endpoint for the freshest prices. Best-effort — missing
    markets are omitted."""
    out: dict[str, tuple[float, float]] = {}
    for mid in market_ids:
        data = market_detail(str(mid))
        if not data or data.get("closed"):
            continue
        prices_raw = _parse_json_field(data, "outcomePrices")
        if len(prices_raw) >= 2:
            try:
                yes = round(float(prices_raw[0]), 4)
                no = round(float(prices_raw[1]), 4)
                out[str(mid)] = (yes, no)
            except (ValueError, TypeError):
                pass
    return out
