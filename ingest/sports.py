"""Sports data fetcher — structured match stats via free APIs.

Sources:
  - TheSportsDB (free tier, 100 req/day) — schedule, results, team info, H2H
  https://www.thesportsdb.com/free-sports-api

No API keys? Falls back to parsing team names for Wikipedia lookups only.
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


def _sportsdb(path: str) -> dict | None:
    if not THESPORTSDB_API_KEY:
        return None
    url = f"{_TSDB_BASE}/{THESPORTSDB_API_KEY}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "altavela/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        log.debug("TheSportsDB failed: %s", exc)
        return None


def _search_team(name: str) -> dict | None:
    """Search for a team by name, return the best match."""
    data = _sportsdb(f"searchteams.php?t={urllib.parse.quote(name)}")
    teams = (data or {}).get("teams")
    if teams and isinstance(teams, list) and len(teams) > 0:
        return teams[0]
    return None


def _last_matches(team_id: str, count: int = 5) -> list[dict]:
    """Get recent results for a team."""
    data = _sportsdb(f"eventslast.php?id={team_id}")
    events = (data or {}).get("results") or []
    results = []
    for e in events[:count]:
        results.append({
            "home": e.get("strHomeTeam", ""),
            "away": e.get("strAwayTeam", ""),
            "home_score": e.get("intHomeScore"),
            "away_score": e.get("intAwayScore"),
            "date": e.get("dateEvent", ""),
        })
    return results


def _h2h(team1: str, team2: str, sport: str = "Soccer") -> list[dict]:
    """Get head-to-head events between two teams."""
    # TheSportsDB doesn't have a direct H2H endpoint, so we search
    # events for team1 and filter for team2
    data = _sportsdb(f"searchevents.php?e={urllib.parse.quote(team1 + ' vs ' + team2)}")
    events = (data or {}).get("event") or []
    # Also try reversed
    data2 = _sportsdb(f"searchevents.php?e={urllib.parse.quote(team2 + ' vs ' + team1)}")
    events2 = (data2 or {}).get("event") or []
    all_events = []
    if isinstance(events, list):
        all_events.extend(events)
    if isinstance(events2, list):
        all_events.extend(events2)
    results = []
    seen = set()
    for e in all_events[:8]:
        key = f"{e.get('strHomeTeam')}-{e.get('strAwayTeam')}-{e.get('dateEvent')}"
        if key in seen:
            continue
        seen.add(key)
        hs = e.get("intHomeScore") or e.get("intScore")
        aways = e.get("intAwayScore")
        results.append({
            "home": e.get("strHomeTeam", ""),
            "away": e.get("strAwayTeam", ""),
            "home_score": hs,
            "away_score": aways,
            "date": e.get("dateEvent", ""),
        })
    return results


def fetch_sports_data(question: str, market: dict | None = None) -> list[str]:
    """Fetch structured sports data for a match question.

    Returns evidence lines with label prefixes:
      [SPORTS-FORM] — recent results
      [SPORTS-H2H] — head-to-head records
      [SPORTS-INFO] — team info
    """
    if not THESPORTSDB_API_KEY:
        return []

    evidence: list[str] = []

    # Extract team names from "X vs Y" or "X - Y" patterns
    parts = re.split(r"\b(?:vs|versus|vs\.)\b", question, flags=re.IGNORECASE)
    if len(parts) < 2:
        # Try "X - Y" (common in Polymarket titles)
        parts = re.split(r"\s{2,}", question.replace(" - ", " vs "), maxsplit=2)
        parts = re.split(r"\b(?:vs|versus|vs\.)\b", parts[0] if parts else question, flags=re.IGNORECASE)
    if len(parts) < 2:
        return []

    name1 = re.split(r"\s*[\(-]", parts[0])[0].strip()
    name2 = re.split(r"\s*[\(-]", parts[1])[0].strip()
    if len(name1) < 3 or len(name2) < 3:
        return []

    # Look up both teams
    t1 = _search_team(name1)
    t2 = _search_team(name2)

    if t1:
        evidence.append(
            f"[SPORTS-INFO] {name1}: {t1.get('strLeague', '')} · "
            f"{t1.get('strStadium', '')} · "
            f"formed {t1.get('intFormedYear', '?')} · "
            f"{t1.get('strDescriptionEN', '')[:150]}")
        # Recent form
        matches = _last_matches(t1.get("idTeam", ""))
        if matches:
            form_str = " · ".join(
                f"{m['home']} {m['home_score']}-{m['away_score']} {m['away']}"
                for m in matches)
            evidence.append(f"[SPORTS-FORM] {name1} last {len(matches)}: {form_str}")

    if t2:
        evidence.append(
            f"[SPORTS-INFO] {name2}: {t2.get('strLeague', '')} · "
            f"{t2.get('strStadium', '')} · "
            f"formed {t2.get('intFormedYear', '?')} · "
            f"{t2.get('strDescriptionEN', '')[:150]}")
        matches = _last_matches(t2.get("idTeam", ""))
        if matches:
            form_str = " · ".join(
                f"{m['home']} {m['home_score']}-{m['away_score']} {m['away']}"
                for m in matches)
            evidence.append(f"[SPORTS-FORM] {name2} last {len(matches)}: {form_str}")

    # Head-to-head
    h2h_events = _h2h(name1, name2)
    if h2h_events:
        h2h_str = " · ".join(
            f"{h['home']} {h['home_score']}-{h['away_score']} {h['away']}"
            for h in h2h_events[:5])
        evidence.append(f"[SPORTS-H2H] {name1} vs {name2}: {h2h_str}")

    return evidence
