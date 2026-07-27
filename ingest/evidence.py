"""Evidence gathering — fetches real-world data for prediction-market research.

Uses Google News RSS (free, no API key) to find recent articles relevant to
a market's question. Returns headlines the researcher can use to ground its
probability estimate in real data instead of memory alone.

Follows the AlphaDesk pattern: code fetches facts, agents interpret them.
"""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("altavela.evidence")


def _search_news(query: str, max_results: int = 8) -> list[str]:
    """Search Google News RSS for articles matching a query.
    Returns list of 'Title — Source' strings. Best-effort."""
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
    sources = re.findall(r'<source.*?>(.*?)</source>', body)

    results = []
    for i, t in enumerate(titles):
        # Skip generic feed titles
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


def gather_evidence(question: str, tags: list[str] | None = None) -> list[str]:
    """Fetch recent news articles relevant to a prediction-market question.
    Returns list of headlines for the researcher to weigh."""
    # Use the question directly as the search query (truncated)
    query = question[:200]
    results = _search_news(query)

    # If no results, try with broader keywords from tags
    if not results and tags:
        query = " ".join(tags[:3])
        results = _search_news(query)

    if results:
        log.debug("Evidence: %d articles for '%s'", len(results), query[:60])
    return results
