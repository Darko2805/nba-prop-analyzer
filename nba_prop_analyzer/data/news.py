"""
NBA news aggregation from public RSS feeds.

RSS is the right tool here (vs. HTML scraping): it's what these sites
publish specifically for machine consumption, so unlike stats.nba.com or
basketball-reference it isn't behind anti-scraping protection — this
fetches live, straight from Render, with a short cache rather than going
through the daily snapshot pipeline.

NBA.com's RSS feed is defunct (redirects to the homepage), so CBS Sports
fills the third slot instead of NBA.com specifically.
"""
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from ..config import REQUEST_HEADERS
from ..cache import cache

_FEEDS = [
    ("ESPN", "https://www.espn.com/espn/rss/nba/news"),
    ("Yahoo Sports", "https://sports.yahoo.com/nba/rss/"),
    ("CBS Sports", "https://www.cbssports.com/rss/headlines/nba/"),
]

_CACHE_KEY = "nba_news_feed"
_CACHE_TTL_SECONDS = 20 * 60
_MAX_ITEMS = 18


def _parse_feed(source: str, xml_text: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date_raw = (item.findtext("pubDate") or "").strip()
        if not title or not link:
            continue

        pub_ts = 0.0
        if pub_date_raw:
            try:
                pub_ts = parsedate_to_datetime(pub_date_raw).timestamp()
            except (ValueError, TypeError):
                pub_ts = 0.0

        items.append({
            "source": source,
            "title": title,
            "link": link,
            "pub_date": pub_date_raw,
            "pub_ts": pub_ts,
        })
    return items


def fetch_news(force: bool = False) -> list[dict]:
    """Returns recent NBA headlines merged from all feeds, newest first, cached ~20min."""
    if not force:
        cached = cache.get(_CACHE_KEY)
        if cached is not None:
            return cached

    all_items = []
    for source, url in _FEEDS:
        try:
            resp = requests.get(url, headers=REQUEST_HEADERS, timeout=10)
            resp.raise_for_status()
            all_items.extend(_parse_feed(source, resp.text))
        except Exception as e:
            print(f"  News fetch failed for {source} ({e}), skipping")

    all_items.sort(key=lambda x: x["pub_ts"], reverse=True)
    result = all_items[:_MAX_ITEMS]
    cache.set(_CACHE_KEY, result)
    return result
