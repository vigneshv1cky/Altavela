"""Evidence gathering — fetches real-world data for prediction-market research.

Sources (all free, no API keys):
  - Google News RSS — recent articles matching the market question
  - Wikipedia API — factual background on the topic
  - Polymarket metadata — market description, resolution source, category
  - Sports-specific — recent form, head-to-head, betting odds/previews

Follows the AlphaDesk pattern: code fetches facts, agents interpret them.
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("altavela.evidence")

_SPORTS_TERMS = re.compile(
    r"\b(vs|versus|win|lose|draw|match|game|tournament|championship|league"
    r"|final|semi.?final|quarter.?final|playoff|series|season"
    r"|spread|home|away|over/under|o/u|score|goal|point|set\b"
    r"|nfl|nba|mlb|nhl|mls|epl|la.?liga|serie.?a|bundesliga|ligue.?1"
    r"|ufc|wwe|f1|nascar|boxing|mma|cricket|rugby"
    r"|counter.?strike|lol|dota|valorant|overwatch|esports"
    r"|itf|atp|wta|grand.?slam|open\b)", re.IGNORECASE)


def _search_news(query: str, max_results: int = 6) -> list[str]:
    """Search Google News RSS for articles matching a query."""
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query, "hl": "en-US", "ceid": "US:en",
    })
    req = urllib.request.Request(url, headers={"User-Agent": "altavela/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        log.debug("Google News RSS failed: %s", exc)
        return []

    titles = re.findall(r"<title>(.*?)</title>", body)
    sources = re.findall(r"<source.*?>(.*?)</source>", body)

    results = []
    for i, t in enumerate(titles):
        if not t or t.startswith("Google News") or t == "Top stories":
            continue
        source = sources[i] if i < len(sources) else ""
        line = t.strip()
        if source and source.strip():
            line += f" — {source.strip()}"
        results.append(line)
        if len(results) >= max_results:
            break
    return results


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

    # 3. Recent news (Google News RSS)
    articles = _search_news(search_terms)
    for a in articles:
        evidence.append(f"[NEWS] {a}")

    # 4. If no news, try broader tag-based search
    if len([e for e in evidence if e.startswith("[NEWS]")]) == 0 and market:
        tags = market.get("tags", [])
        if tags:
            articles = _search_news(" ".join(tags[:3]))
            for a in articles:
                evidence.append(f"[NEWS] {a}")

    # 5. Sports-specific: recent form, H2H, odds/previews
    if _SPORTS_TERMS.search(question):
        tags_str = " ".join(market.get("tags", [])[:2]) if market else ""
        base = f"{question[:120]} {tags_str}".strip()
        for suffix, label in [
            ("recent results form last 5 matches", "[FORM]"),
            ("head to head record history", "[H2H]"),
            ("betting odds prediction preview", "[ODDS]"),
        ]:
            query = f"{base} {suffix}"[:250]
            for a in _search_news(query, max_results=3):
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
