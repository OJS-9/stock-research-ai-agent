"""
News briefing service — fetches financial articles via Nimble agents (Bloomberg + Morningstar + WSJ)
as primary source, and web search (WSJ + Reuters) as secondary on demand.
Results are cached in memory with a 15-minute TTL per cache slot.
"""

import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict

logger = logging.getLogger(__name__)

CACHE_TTL = 15 * 60  # 15 minutes

# Bloomberg agent regenerated 2026-06-20: the old query-keyword agent had a
# ~50% empty-result rate (a network-capture race); the regenerated agent takes a
# full search URL and is ~90% reliable. See issue #146.
BLOOMBERG_AGENT = "bloomberg_search_2026_06_20_regen"
MORNINGSTAR_AGENT = "morningstar_search_2026_02_23_zicq0zdj_02869390"
WSJ_AGENT = "wsj_article_template_2026_03_02_z7hhhvxe"
WSJ_PIPELINE = "WSJcomUSBusiness"
SEARCH_QUERY = "markets stocks economy finance"
BLOOMBERG_SEARCH_URL = "https://www.bloomberg.com/search?query=" + urllib.parse.quote(SEARCH_QUERY)

_cache = {
    "primary": {"articles": [], "fetched_at": None},
    "more": {"articles": [], "fetched_at": None},
}


def get_briefing() -> List[Dict]:
    now = time.time()
    slot = _cache["primary"]
    if slot["fetched_at"] is None or (now - slot["fetched_at"]) > CACHE_TTL:
        _refresh_primary()
    return _cache["primary"]["articles"]


def get_more() -> List[Dict]:
    now = time.time()
    slot = _cache["more"]
    if slot["fetched_at"] is None or (now - slot["fetched_at"]) > CACHE_TTL:
        _refresh_more()
    return _cache["more"]["articles"]


def force_refresh() -> None:
    """Reset cache TTL so next call fetches fresh news."""
    _cache["primary"]["fetched_at"] = None
    _cache["more"]["fetched_at"] = None


def _refresh_primary() -> None:
    try:
        from nimble_client import NimbleClient

        client = NimbleClient()
    except Exception:
        logger.warning("news refresh failed: NimbleClient unavailable")
        _cache["primary"]["fetched_at"] = time.time()
        return

    try:
        _do_refresh_primary(client)
    except Exception as e:
        logger.warning("news refresh failed: %s", e)
    _cache["primary"]["fetched_at"] = time.time()


def _do_refresh_primary(client) -> None:
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_bloomberg = pool.submit(client.run_agent, BLOOMBERG_AGENT, {"url": BLOOMBERG_SEARCH_URL})
        f_morningstar = pool.submit(client.run_agent, MORNINGSTAR_AGENT, {"search_term": SEARCH_QUERY})
        f_wsj = pool.submit(client.run_agent, WSJ_AGENT, {"feed_name": WSJ_PIPELINE})
        bloomberg_results = f_bloomberg.result()
        morningstar_results = f_morningstar.result()
        wsj_results = f_wsj.result()

    # Map, then drop records with no headline. Nimble agents intermittently
    # return empty placeholder records (an empty {} normalizes to [{}]); without
    # this filter those render as blank "ghost" cards in the briefing.
    # Finally sort by image presence so image articles lead within each source
    # without collapsing all no-image sources to the end.
    def _sort_by_image(items, publisher):
        mapped = [_map_article(item, publisher) for item in items]
        mapped = [a for a in mapped if a["title"]]
        mapped.sort(key=lambda a: 0 if a["image"] else 1)
        return mapped

    sources = [
        ("Bloomberg", _sort_by_image(bloomberg_results, "Bloomberg")),
        ("Morningstar", _sort_by_image(morningstar_results, "Morningstar")),
        ("WSJ", _sort_by_image(wsj_results, "WSJ")),
    ]

    # Surface degradation: an agent that returns HTTP 200 with empty results is
    # otherwise silent (nimble_client only logs on exceptions), so a degraded or
    # empty briefing would have no signal at all.
    for name, items in sources:
        if not items:
            logger.warning("news refresh: source '%s' returned no usable articles", name)
    if all(not items for _, items in sources):
        logger.error("news refresh: all news sources returned no usable articles; briefing is empty")

    # Round-robin interleave: Bloomberg → Morningstar → WSJ
    source_lists = [items for _, items in sources]
    indices = [0, 0, 0]
    interleaved = []
    while True:
        added = False
        for i, items in enumerate(source_lists):
            if indices[i] < len(items):
                interleaved.append(items[indices[i]])
                indices[i] += 1
                added = True
        if not added:
            break

    _cache["primary"]["articles"] = interleaved


def _map_article(item: Dict, publisher: str) -> Dict:
    headline = item.get("headline", item.get("header", item.get("title", ""))).strip()
    article_url = item.get("article_url", "").strip()
    if not article_url:
        if publisher == "Bloomberg":
            encoded = urllib.parse.urlencode({"query": headline})
            article_url = f"https://www.bloomberg.com/search?{encoded}"
        elif publisher == "WSJ":
            encoded = urllib.parse.urlencode({"query": headline})
            article_url = f"https://www.wsj.com/search?{encoded}"
        else:
            encoded = urllib.parse.urlencode({"q": headline})
            article_url = f"https://www.morningstar.com/search?{encoded}"

    return {
        "title": headline,
        "description": item.get("summary", item.get("description", "")).strip(),
        "image": item.get("image_url", ""),
        "category": item.get("category", "Markets"),
        "publisher": publisher,
        "url": article_url,
    }


def _refresh_more() -> None:
    try:
        from nimble_client import NimbleClient

        client = NimbleClient()
    except Exception:
        return

    secondary_sources = [
        {
            "name": "WSJ",
            "query": "site:wsj.com markets stocks earnings economy finance",
        },
        {
            "name": "Reuters",
            "query": "site:reuters.com markets stocks earnings economy finance",
        },
    ]

    articles = []
    for source in secondary_sources:
        try:
            result = client.search(source["query"], num_results=10, topic="news")
            results = result.get("results") or []
            for item in results:
                title = item.get("title", "").strip()
                url = item.get("url", "").strip()
                if title and url:
                    articles.append(
                        {
                            "title": title,
                            "description": item.get("description", "").strip(),
                            "image": "",
                            "category": "Markets",
                            "publisher": source["name"],
                            "url": url,
                        }
                    )
        except Exception:
            continue

    _cache["more"]["articles"] = articles
    _cache["more"]["fetched_at"] = time.time()
