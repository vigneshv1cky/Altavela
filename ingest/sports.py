"""Sports data fetcher — structured match stats via TheSportsDB free API.

Free tier limitations (key=123):
  - searchteams.php → returns Arsenal only (basically useless)
  - eventslast.php → home events only
  - searchevents.php → full access
  - lookupteam.php, lookupevent.php → full access with IDs

Strategy: search events by match name for H2H data, skip team search.
Wikipedia fallback in evidence.py handles team background.
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from altavela.config import THESPORTSDB_API_KEY

log = logging.getLogger("altavela.sports")

_TSDB_BASE = "https://www.thesportsdb.com/api/v1/json"
# "123" is the free tier key with limitations noted above
_IS_PREMIUM = THESPORTSDB_API_KEY not in ("", "123")


def _sportsdb(path: str) -> dict | None:
    url = f"{_TSDB_BASE}/{THESPORTSDB_API_KEY}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "altavela/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        log.debug("TheSportsDB failed: %s", exc)
        return None


def _search_events(query: str) -> list[dict]:
    """Search events by name — works on free tier."""
    data = _sportsdb(f"searchevents.php?e={urllib.parse.quote(query)}")
    events = (data or {}).get("event")
    if not events:
        return []
    if isinstance(events, dict):
        events = [events]
    return [e for e in events if isinstance(e, dict)][:10]


def fetch_sports_data(question: str, market: dict | None = None) -> list[str]:
    """Fetch structured sports data for a match question.

    Uses TheSportsDB searchevents.php (works on free tier) to find
    head-to-head records between teams. Returns evidence lines:

      [SPORTS-H2H] — head-to-head results
      [SPORTS-INFO] — event/team details
    """
    evidence: list[str] = []

    # Extract team names from "X vs Y" patterns
    parts = re.split(r"\b(?:vs|versus|vs\.)\b", question, flags=re.IGNORECASE)
    if len(parts) < 2:
        return []

    name1 = re.split(r"\s*[\(-]", parts[0])[0].strip()
    name2 = re.split(r"\s*[\(-]", parts[1])[0].strip()
    if len(name1) < 3 or len(name2) < 3:
        return []

    # Search events: "TeamA vs TeamB"
    h2h_events = _search_events(f"{name1} vs {name2}")
    if not h2h_events:
        h2h_events = _search_events(f"{name2} vs {name1}")

    if h2h_events:
        results = []
        for e in h2h_events[:6]:
            home = e.get("strHomeTeam", "")
            away = e.get("strAwayTeam", "")
            hs = e.get("intHomeScore") or e.get("intScore", "?")
            aways = e.get("intAwayScore", "?")
            date = e.get("dateEvent", "")[:10]
            results.append(f"{home} {hs}-{aways} {away} ({date})")
        evidence.append(f"[SPORTS-H2H] {name1} vs {name2}: {' · '.join(results)}")

    # Also search for each team's recent events (home only on free tier)
    if _IS_PREMIUM:
        for name in [name1, name2]:
            team_events = _search_events(name)
            if team_events:
                recent = []
                for e in team_events[:5]:
                    home = e.get("strHomeTeam", "")
                    away = e.get("strAwayTeam", "")
                    hs = e.get("intHomeScore") or e.get("intScore", "?")
                    aways = e.get("intAwayScore", "?")
                    date = e.get("dateEvent", "")[:10]
                    recent.append(f"{home} {hs}-{aways} {away} ({date})")
                evidence.append(f"[SPORTS-FORM] {name} recent: {' · '.join(recent)}")

    return evidence
