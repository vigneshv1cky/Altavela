"""Evidence gathering — fetches real-world data for prediction-market research.

Sources (all free, no API keys):
  - DuckDuckGo web search — real search results with snippets
  - Wikipedia API — factual background + team page extracts
  - Polymarket metadata — market description, resolution source, category
  - TheSportsDB — match results for head-to-head data
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from ddgs import DDGS

log = logging.getLogger("altavela.evidence")

_SPORTS_TERMS = re.compile(
    r"\b(vs|versus|win|lose|draw|match|game|tournament|championship|league"
    r"|final|semi.?final|quarter.?final|playoff|series|season"
    r"|spread|home|away|over/under|o/u|score|goal|point|set\b"
    r"|nfl|nba|mlb|nhl|mls|epl|la.?liga|serie.?a|bundesliga|ligue.?1"
    r"|ufc|wwe|f1|nascar|boxing|mma|cricket|rugby"
    r"|counter.?strike|lol|dota|valorant|overwatch|esports"
    r"|itf|atp|wta|grand.?slam|open\b)", re.IGNORECASE)


def _web_search(query: str, max_results: int = 6) -> list[str]:
    """Search the web via DuckDuckGo — returns title + snippet."""
    try:
        with DDGS() as ddgs:
            results = []
            for r in ddgs.text(query, max_results=max_results):
                body = r.get("body", "")
                title = r.get("title", "")
                line = title
                if body:
                    line += f" — {body[:200]}"
                results.append(line)
            return results
    except Exception as exc:
        log.debug("Web search failed: %s", exc)
        return []


def _wikipedia_summary(query: str) -> str | None:
    """Get a short Wikipedia extract for a search term. No API key needed."""
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "list": "search",
        "srsearch": query, "srlimit": "1", "srprop": "snippet",
        "origin": "*",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "altavela/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        log.debug("Wikipedia API failed: %s", exc)
        return None

    results = (data.get("query") or {}).get("search") or []
    if results:
        snippet = results[0].get("snippet", "")
        snippet = re.sub(r"<[^>]+>", "", snippet)
        if snippet:
            return snippet[:300]
    return None


def _wikipedia_extract(title: str) -> str | None:
    """Fetch the intro extract of a Wikipedia page by title."""
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "extracts",
        "exintro": "1", "explaintext": "1",
        "titles": title, "origin": "*",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "altavela/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        log.debug("Wikipedia extract failed: %s", exc)
        return None
    pages = (data.get("query") or {}).get("pages") or {}
    for _pid, info in pages.items():
        extract = info.get("extract", "")
        if extract:
            return extract[:500]
    return None


def _polymarket_context(market: dict) -> str | None:
    """Extract useful metadata from a Polymarket market dict."""
    parts = []
    desc = market.get("description", "")
    if desc and len(desc) > 10:
        parts.append(f"Market description: {desc[:300]}")

    resolution = market.get("resolutionSource", "")
    if resolution:
        parts.append(f"Resolution source: {resolution[:100]}")

    # Category/tags
    tags = market.get("tags", []) or []
    if tags:
        parts.append(f"Tags: {', '.join(tags[:5])}")

    end_date = market.get("end_date", "")
    if end_date:
        parts.append(f"Resolution deadline: {end_date}")

    return " | ".join(parts) if parts else None


def gather_evidence(question: str, market: dict | None = None) -> list[str]:
    """Fetch real-world evidence for a prediction-market question.

    Returns list of evidence lines for the researcher. Each line is a
    distinct fact or article headline. Code fetches, agent interprets."""
    evidence: list[str] = []

    # 1. Polymarket's own context (free metadata)
    if market:
        ctx = _polymarket_context(market)
        if ctx:
            evidence.append(f"[MARKET] {ctx}")

    # 2. Wikipedia background (free API, factual)
    # Extract key terms from the question for a focused search
    search_terms = question[:200]
    wiki = _wikipedia_summary(search_terms)
    if wiki:
        evidence.append(f"[WIKI] {wiki}")

    # 3. Web search (DuckDuckGo — real results with snippets)
    # Generate targeted queries — use question plus tag-based variations
    queries = [question[:200]]
    if market:
        tags = market.get("tags", []) or []
        if tags:
            queries.append(" ".join(tags[:3]))
        # For sports: add "match" / "vs" specific queries
        if _SPORTS_TERMS.search(question):
            parts = re.split(r"\b(?:vs|versus|vs\.)\b", question, flags=re.IGNORECASE)
            if len(parts) >= 2:
                n1 = re.split(r"\s*[\(-]", parts[0])[0].strip()
                n2 = re.split(r"\s*[\(-]", parts[1])[0].strip()
                if len(n1) > 2 and len(n2) > 2:
                    queries.append(f'"{n1}" "{n2}" match result')
                    queries.append(f"{n1} {n2} h2h prediction odds")

    seen = set()
    for q in queries:
        for a in _web_search(q.strip()[:200], max_results=4):
            key = a[:80]
            if key in seen:
                continue
            seen.add(key)
            evidence.append(f"[SEARCH] {a}")
            if len(seen) >= 8:
                break
        if len(seen) >= 8:
            break

    # 5. Sports-specific: structured API data, recent form, H2H, odds
    if _SPORTS_TERMS.search(question):
        # Structured sports data (TheSportsDB — needs API key)
        try:
            from altavela.ingest.sports import fetch_sports_data
            sports_lines = fetch_sports_data(question, market)
            evidence.extend(sports_lines)
        except Exception as exc:
            log.debug("Sports data fetch failed: %s", exc)
        tags_str = " ".join(market.get("tags", [])[:2]) if market else ""
        base = f"{question[:120]} {tags_str}".strip()
        for suffix, label in [
            ("recent results form last 5 matches", "[FORM]"),
            ("head to head record history", "[H2H]"),
            ("betting odds prediction preview", "[ODDS]"),
        ]:
            query = f"{base} {suffix}"[:250]
            for a in _web_search(query, max_results=3):
                evidence.append(f"{label} {a}")

        # Sports Wikipedia: look up team pages for roster/form data
        for part in re.split(r"\b(?:vs|versus|vs\.)\b", question, flags=re.IGNORECASE):
            part = part.strip().rstrip("?")
            if not part or len(part) < 4:
                continue
            # Remove common suffixes like (BO3), - Map 2, etc.
            name = re.split(r"\s*[\(-]", part)[0].strip()
            if len(name) < 4:
                continue
            extract = _wikipedia_extract(name)
            if extract:
                evidence.append(f"[TEAM] {name}: {extract}")

    if evidence:
        log.info("Evidence: %d items for '%s'", len(evidence), question[:60])
    return evidence
