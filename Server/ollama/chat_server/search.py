"""Web search (DuckDuckGo via ddgs, Wikipedia as fallback) and page reading."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Iterable, List, Optional
from urllib.parse import quote, urlparse

import httpx

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Hit:
    title: str
    link: str
    snippet: str
    text: str = ""          # page body, filled in by fetch_pages
    source: str = "duckduckgo"


@dataclass
class _RateLimit:
    interval: float
    last: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def wait(self):
        async with self.lock:
            delay = self.interval - (time.monotonic() - self.last)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last = time.monotonic()


_ddg_limit: Optional[_RateLimit] = None


def _limit() -> _RateLimit:
    global _ddg_limit
    if _ddg_limit is None:
        _ddg_limit = _RateLimit(Config.SEARCH_MIN_INTERVAL)
    return _ddg_limit


async def duckduckgo(query: str, n: int) -> List[Hit]:
    def _sync():
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            rows = ddgs.text(query, max_results=n) or []
        return [
            Hit(r.get("title") or "", r.get("href") or r.get("link") or "", r.get("body") or r.get("snippet") or "")
            for r in rows
        ]

    await _limit().wait()
    return await asyncio.to_thread(_sync)


async def wikipedia(http: httpx.AsyncClient, query: str, n: int) -> List[Hit]:
    r = await http.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query, "srlimit": n, "format": "json"},
        timeout=15,
    )
    r.raise_for_status()
    hits = []
    for item in r.json().get("query", {}).get("search", []):
        title = item.get("title") or query
        snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
        hits.append(Hit(title, f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}", snippet, source="wikipedia"))
    return hits


async def search(http: httpx.AsyncClient, query: str, n: int = Config.SEARCH_RESULTS) -> List[Hit]:
    """Search one query; DuckDuckGo first, Wikipedia if that fails or is empty."""
    try:
        hits = await duckduckgo(query, n)
        if hits:
            return hits
    except Exception as e:
        log.warning("DuckDuckGo search failed for %r: %s", query, e)
    try:
        return await wikipedia(http, query, n)
    except Exception as e:
        log.warning("Wikipedia search failed for %r: %s", query, e)
        return []


async def search_many(http: httpx.AsyncClient, queries: Iterable[str], n: int = Config.SEARCH_RESULTS) -> List[Hit]:
    """Run several queries concurrently and merge them, round-robin, without duplicate URLs."""
    queries = [q for q in dict.fromkeys(q.strip() for q in queries) if q]
    results = await asyncio.gather(*(search(http, q, n) for q in queries))
    merged, seen = [], set()
    for rank in range(max((len(r) for r in results), default=0)):
        for hits in results:
            if rank < len(hits):
                h = hits[rank]
                key = h.link.rstrip("/")
                if h.link and key not in seen:
                    seen.add(key)
                    merged.append(h)
    return merged[:n]


_DROP_TAGS = ["script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe", "template"]


def html_to_text(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    lines = (ln.strip() for ln in root.get_text("\n").splitlines())
    text = "\n".join(ln for ln in lines if ln)
    return re.sub(r"\n{3,}", "\n\n", text)


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


async def fetch_page(http: httpx.AsyncClient, url: str, max_chars: int = Config.SEARCH_PAGE_CHARS) -> dict:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs can be fetched")
    r = await http.get(url, timeout=Config.FETCH_TIMEOUT, follow_redirects=True,
                       headers={"User-Agent": Config.USER_AGENT, "Accept-Language": "en"})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "html" in ctype or not ctype:
        html = r.text
        text, title = html_to_text(html), page_title(html)
    elif ctype.startswith("text/") or "json" in ctype:
        text, title = r.text, ""
    elif "pdf" in ctype:
        from .documents import extract_text
        text, title = extract_text(url, r.content, "application/pdf"), ""
    else:
        raise ValueError(f"unsupported content type {ctype}")
    return {"url": str(r.url), "title": title, "text": text[:max_chars], "truncated": len(text) > max_chars}


async def fetch_pages(http: httpx.AsyncClient, hits: List[Hit], count: int = Config.SEARCH_FETCH_PAGES) -> None:
    """Read the top `count` result pages in parallel. Pages that fail keep their snippet only."""
    async def one(h: Hit):
        try:
            h.text = (await fetch_page(http, h.link))["text"]
        except Exception as e:
            log.info("could not read %s: %s", h.link, e)

    await asyncio.gather(*(one(h) for h in hits[:count]))
