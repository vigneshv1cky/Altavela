"""Altavela scout — scans prediction markets and picks the highest-edge opportunities."""

import json
import logging

from altavela.config import MAX_PICKS_PER_WINDOW
from altavela.llm import call_role, wrap_data

log = logging.getLogger("altavela.scout")

_SYSTEM = (
    "You are the scout desk of a prediction-market research firm. Your team "
    "trades binary outcome contracts (BUY YES / BUY NO) on markets like Polymarket. "
    "You allocate the team's scarce attention.\n\n"
    "You receive a list of active prediction markets with current prices, volume, "
    "and liquidity. Your job: pick up to {max_picks} markets that most merit full "
    "team analysis — where the CURRENT market price likely differs from the TRUE "
    "probability.\n\n"
    "What makes a market worth analyzing:\n"
    "  • MISPRICING: the price seems off vs available evidence (polls, data, news)\n"
    "  • INFORMATION: breaking news/events not yet fully priced in\n"
    "  • CALENDAR: event approaching resolution, market hasn't caught up\n"
    "  • MOMENTUM: strong price trend or whale activity suggesting a move\n\n"
    "For each pick, give a one-sentence reason. For each SKIP, also give a reason.\n"
    "Prefer HIGH VOLUME, HIGH LIQUIDITY markets.\n"
    "Near-expiry markets (<1h): only pick if you see a clear last-minute mispricing.\n"
    "Extreme prices (<5¢ or >95¢): the edge is tiny — pick only if strongly confident.\n\n"
    "edge_hint: MISPRICING | INFORMATION | CALENDAR | MOMENTUM\n\n"
    'Return ONLY JSON: {{"picks": [{{"market_id": "...", "question": "...", '
    '"edge_hint": "MISPRICING|INFORMATION|CALENDAR|MOMENTUM", "direction": '
    '"BUY_YES|BUY_NO", "reason": "..."}}], '
    '"skips": [{{"market_id": "...", "reason": "..."}}]}}'
)

_SCHEMA = {
    "picks": {
        "type": list, "maxitems": MAX_PICKS_PER_WINDOW,
        "items": {
            "market_id": {"type": str, "maxlen": 100},
            "question": {"type": str, "maxlen": 300},
            "edge_hint": {"type": str, "enum": ["MISPRICING", "INFORMATION", "CALENDAR", "MOMENTUM"]},
            "direction": {"type": str, "enum": ["BUY_YES", "BUY_NO"]},
            "reason": {"type": str, "maxlen": 300},
        },
    },
    "skips": {
        "type": list, "optional": True, "maxitems": 100,
        "items": {
            "market_id": {"type": str, "maxlen": 100},
            "reason": {"type": str, "maxlen": 200},
        },
    },
}


def run_scout(markets: list[dict]) -> dict:
    """markets: list of active market dicts. Returns {picks, skips}."""
    if not markets:
        return {"picks": [], "skips": []}

    from altavela.config import SCOUT_MAX_CANDIDATES

    if len(markets) > SCOUT_MAX_CANDIDATES:
        markets = markets[:SCOUT_MAX_CANDIDATES]

    lines = []
    for m in markets:
        lines.append(json.dumps({
            "market_id": m["id"],
            "question": m["question"],
            "outcomes": m.get("outcomes", []),
            "prices": m.get("prices", []),
            "volume": m.get("volume", 0),
            "liquidity": m.get("liquidity", 0),
            "end_date": m.get("end_date", ""),
            "category": m.get("category", ""),
            "tags": m.get("tags", []),
        }))

    user = (
        "Active prediction markets (sorted by volume):\n"
        + wrap_data("markets", "\n".join(lines))
    )
    return call_role(
        "scout", _SYSTEM.format(max_picks=MAX_PICKS_PER_WINDOW),
        user, schema=_SCHEMA,
    )
