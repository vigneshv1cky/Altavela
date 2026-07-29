"""Evidence gathering — fetches real-world data for prediction-market research.

Sources:
  - DuckDuckGo web search — real search results with 400-char snippets
  - Smart query generation — extracts team names, tries quoted + targeted queries
  - Sports: [FORM]/[H2H]/[ODDS] targeted extra searches
  - Polymarket metadata — market description, tags, resolution source

Code fetches facts, LLM agents interpret them. No API keys needed.
"""

import logging
import re

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
        with DDGS(timeout=15) as ddgs:
            results = []
            for r in ddgs.text(query, max_results=max_results):
                body = r.get("body", "")
                title = r.get("title", "")
                line = title
                if body:
                    line += f" — {body[:400]}"
                results.append(line)
            return results
    except Exception as exc:
        msg = str(exc).lower()
        if "ratelimit" in msg or "403" in msg or "block" in msg:
            log.warning("DuckDuckGo rate-limited/blocked — backing off")
        else:
            log.warning("Web search failed: %s", exc)
        return []


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

    # 2. Web search (DuckDuckGo — real results with snippets)
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

    # 4. Sports-specific: targeted searches for form, H2H, odds
    if _SPORTS_TERMS.search(question):
        tags_str = " ".join(market.get("tags", [])[:2]) if market else ""
        for suffix, label in [
            ("results last 5 matches", "[FORM]"),
            ("head to head history", "[H2H]"),
            ("odds prediction", "[ODDS]"),
        ]:
            q = f"{question[:100]} {tags_str} {suffix}"[:200]
            for a in _web_search(q, max_results=3):
                evidence.append(f"{label} {a}")

    if evidence:
        n_web = len([e for e in evidence if not e.startswith("[MARKET]")])
        log.info("Evidence: %d items (%d web) for '%s'", len(evidence), n_web, question[:60])
    return evidence
