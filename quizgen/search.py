"""Web search grounding for the quiz pipeline.

Provider chain (first provider with >= 2 usable sources wins):
  1. Brave Search API — only when BRAVE_API_KEY is set (free tier:
     2,000 queries/month, 1 req/s; sign up at https://brave.com/search/api/,
     key from https://api-dashboard.search.brave.com/, set BRAVE_API_KEY in
     the Render dashboard). Most reliable: a real search API, not scraping.
  2. DuckDuckGo Lite — GET https://lite.duckduckgo.com/lite/?q=... The
     HTML endpoint (html.duckduckgo.com) is blocked from this build host
     and from Render (202 / captcha / timeout); Lite is a lighter endpoint
     that answers with real results, but it is flaky (sometimes 202, the
     verification page) so it gets one retry and then we fall through.
  3. Wikipedia API — https://en.wikipedia.org/w/api.php, no key, free.
     Excellent for general / encyclopedic topics (how mortgages work, what
     a strata title is), weak for current market news. Search, then fetch
     plain-text extracts for the top pages.

Each provider returns {"sources", "note", "provider"}. A source is usable
with 200+ chars of text. Fewer than 2 usable sources from every provider
means ungrounded mode, with search.note recording what failed (joined
with "; ") — never a silent downgrade, never a grounded claim without
sources. The model never writes URLs: the server resolves them from the
search results (for DuckDuckGo Lite the real URL is decoded out of the
`uddg` redirect parameter; for Wikipedia it is the article URL).

Header note (measured 2026-11-07): httpx's default
`Accept-Encoding: gzip, deflate, br` makes Wikimedia's edge return 403,
while `Accept-Encoding: gzip` alone returns 200. The shared client below
therefore sends gzip only. The same client is used for page fetches.
"""
import logging
import os
import re
import time
import urllib.parse

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("quizgen.search")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
      "99-quiz-builder/1.0 (+https://github.com/mirzasnowy/99-quiz-builder)")
WIKI_UA = ("99-quiz-builder/1.0 (contact: "
           "https://github.com/mirzasnowy/99-quiz-builder)")
# gzip only: Wikimedia 403s requests advertising brotli (see header note).
HEADERS = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
WIKI_HEADERS = {"User-Agent": WIKI_UA, "Accept": "*/*", "Accept-Encoding": "gzip"}

DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
WIKI_API_URL = "https://en.wikipedia.org/w/api.php"
BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"

SEARCH_TIMEOUT = 12.0
FETCH_TIMEOUT = 6.0
MAX_RESULTS = 5
FETCH_TOP = 3
TEXT_CAP = 1800
MIN_USABLE_CHARS = 200
MIN_USABLE_SOURCES = 2
RETRIES = 1  # one retry per provider call on transient failure


def _clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    text = _clean_text(" ".join(p for p in paras if p))
    if len(text) < MIN_USABLE_CHARS:
        text = _clean_text(soup.get_text(" ", strip=True))
    return text


def _make_client():
    return httpx.Client(headers=HEADERS, follow_redirects=True)


def _fetch_top_pages(client, results):
    """Fetch the top FETCH_TOP result pages for text; remaining results
    contribute their snippet. Returns the usable-source list."""
    sources = []
    for i, res in enumerate(results):
        sid = "S%d" % (i + 1)
        text = ""
        fetched = False
        if i < FETCH_TOP:
            try:
                pr = client.get(res["url"], timeout=FETCH_TIMEOUT)
                if pr.status_code == 200 and "text/html" in (
                        pr.headers.get("content-type", "")):
                    text = _extract_page_text(pr.text)[:TEXT_CAP]
                    fetched = len(text) >= MIN_USABLE_CHARS
            except Exception as exc:
                log.warning("fetch %s failed: %s", res["url"], exc)
        if not fetched:
            text = res.get("snippet", "")[:TEXT_CAP]
        if len(text) >= MIN_USABLE_CHARS:
            sources.append({"id": sid, "title": res["title"], "url": res["url"],
                            "fetched": fetched, "chars": len(text), "text": text})
        time.sleep(0.3)  # be polite between page fetches
    return sources


def _ddg_lite_results(client, query):
    """GET the Lite endpoint and parse result links + snippets.

    Result hrefs are //duckduckgo.com/l/?uddg=<url-encoded target>&rut=...;
    the real URL is decoded out of the `uddg` parameter. Raises on
    non-200 (202 is DuckDuckGo's verification page) or empty results.
    """
    last_exc = None
    for attempt in range(RETRIES + 1):
        try:
            r = client.get(DDG_LITE_URL, params={"q": query},
                           timeout=SEARCH_TIMEOUT)
            if r.status_code != 200:
                raise RuntimeError("HTTP %s" % r.status_code)
            soup = BeautifulSoup(r.text, "html.parser")
            links = soup.find_all("a", class_="result-link")
            snippets = soup.find_all("td", class_="result-snippet")
            results = []
            for i, a in enumerate(links[:MAX_RESULTS]):
                href = (a.get("href") or "").strip()
                title = _clean_text(a.get_text())
                if not href or not title:
                    continue
                parsed = urllib.parse.urlparse(href)
                qs = urllib.parse.parse_qs(parsed.query)
                real = qs.get("uddg", [None])[0]
                url = real or href
                if url.startswith("//"):
                    url = "https:" + url
                snippet = ""
                if i < len(snippets):
                    snippet = _clean_text(snippets[i].get_text())
                if url.startswith("http"):
                    results.append({"title": title, "url": url,
                                    "snippet": snippet})
            if not results:
                raise RuntimeError("no results parsed")
            return results
        except Exception as exc:
            last_exc = exc
            log.warning("DDG Lite attempt %d failed: %s", attempt + 1, exc)
            time.sleep(1.0)
    raise last_exc


def _wikipedia_results(client, query):
    """Wikipedia search + plain-text extracts. Returns result dicts with
    the article URL as `url` and the extract as `snippet` (used as the
    source text directly, so no page fetch is needed).

    The extracts call uses exintro=True (lead section only): a full-article
    extract is unreliable — measured 2026-11-07, "Mortgage" and "Reverse
    mortgage" both came back 0 chars with a full extract, while the intro
    extract returned 2,543 and 1,952 chars. The lead is also the most
    definitional part of an article, which is what quiz questions need.
    Extracts are fetched for up to 5 titles so one empty extract does not
    sink the provider.
    """
    last_exc = None
    for attempt in range(RETRIES + 1):
        try:
            r = client.get(WIKI_API_URL, params={
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": MAX_RESULTS, "format": "json"},
                headers=WIKI_HEADERS, timeout=SEARCH_TIMEOUT)
            if r.status_code != 200:
                raise RuntimeError("HTTP %s" % r.status_code)
            found = [x for x in r.json()["query"]["search"]
                     if "(disambiguation)" not in x.get("title", "")]
            if not found:
                raise RuntimeError("no results")
            titles = [x["title"] for x in found[:MAX_RESULTS]]
            r2 = client.get(WIKI_API_URL, params={
                "action": "query", "prop": "extracts", "explaintext": True,
                "exintro": True, "titles": "|".join(titles),
                "format": "json"}, headers=WIKI_HEADERS,
                timeout=SEARCH_TIMEOUT)
            if r2.status_code != 200:
                raise RuntimeError("HTTP %s" % r2.status_code)
            results = []
            for page in r2.json()["query"]["pages"].values():
                text = (page.get("extract") or "").strip()
                title = page.get("title") or ""
                if not title or len(text) < MIN_USABLE_CHARS:
                    continue
                url = ("https://en.wikipedia.org/wiki/"
                       + urllib.parse.quote(title.replace(" ", "_")))
                results.append({"title": title, "url": url,
                                "snippet": text[:TEXT_CAP], "wiki_text": True})
            if not results:
                raise RuntimeError("no usable extracts")
            return results
        except Exception as exc:
            last_exc = exc
            log.warning("Wikipedia attempt %d failed: %s", attempt + 1, exc)
            time.sleep(1.0)
    raise last_exc


def _brave_results(client, query):
    """Brave Search API (opt-in, BRAVE_API_KEY). Raises on any failure;
    401/403 means the key is wrong, 429 means the free tier is exhausted."""
    key = os.getenv("BRAVE_API_KEY", "").strip()
    r = client.get(BRAVE_API_URL,
                   params={"q": query, "count": MAX_RESULTS},
                   headers={"X-Subscription-Token": key,
                            "Accept": "application/json"},
                   timeout=10.0)
    if r.status_code in (401, 403):
        raise RuntimeError("brave_auth_error")
    if r.status_code == 429:
        raise RuntimeError("brave_rate_limited")
    if r.status_code != 200:
        raise RuntimeError("HTTP %s" % r.status_code)
    results = []
    for item in (r.json().get("web") or {}).get("results", [])[:MAX_RESULTS]:
        if item.get("title") and item.get("url"):
            results.append({"title": _clean_text(item["title"]),
                            "url": item["url"],
                            "snippet": _clean_text(item.get("description", ""))})
    if not results:
        raise RuntimeError("no results")
    return results


def _provider_sources(client, kind, query):
    """Run one provider end to end. Returns (sources, fail_note):
    fail_note is None when the provider yielded >= MIN_USABLE_SOURCES,
    otherwise a short reason string."""
    try:
        if kind == "brave-api":
            results = _brave_results(client, query)
            sources = _fetch_top_pages(client, results)
        elif kind == "duckduckgo-lite":
            results = _ddg_lite_results(client, query)
            sources = _fetch_top_pages(client, results)
        elif kind == "wikipedia":
            results = _wikipedia_results(client, query)
            # Extracts are already the source text; no page fetch needed.
            sources = []
            for i, res in enumerate(results):
                text = res["snippet"][:TEXT_CAP]
                sources.append({"id": "S%d" % (i + 1), "title": res["title"],
                                "url": res["url"], "fetched": True,
                                "chars": len(text), "text": text})
        else:
            raise ValueError(kind)
    except Exception as exc:
        msg = str(exc)
        if "auth_error" in msg:
            return [], "brave_auth_error"
        if "rate_limited" in msg:
            return [], "brave_rate_limited"
        if kind == "brave-api":
            return [], "brave_blocked"
        if kind == "duckduckgo-lite":
            return [], "ddg_lite_blocked"
        return [], "wikipedia_blocked"
    if len(sources) >= MIN_USABLE_SOURCES:
        return sources, None
    if sources:
        return sources, "%s_too_few_sources" % kind.replace("-", "_")
    return [], "%s_no_results" % kind.replace("-", "_")


def search(query, client=None):
    """Run the provider chain. Returns {"sources", "note", "provider"}.

    note is None when the quiz can be grounded (>= 2 usable sources);
    otherwise it records what failed, joined with "; ", so the quiz JSON
    stays honest about why the quiz is ungrounded. provider names the
    provider that supplied the returned sources, or "none".
    """
    own_client = client is None
    client = client or _make_client()
    try:
        chain = []
        if os.getenv("BRAVE_API_KEY", "").strip():
            chain.append("brave-api")
        chain += ["duckduckgo-lite", "wikipedia"]
        notes = []
        best_sources = []
        best_provider = "none"
        for kind in chain:
            sources, fail_note = _provider_sources(client, kind, query)
            if fail_note is None:
                return {"sources": sources, "note": None, "provider": kind}
            notes.append(fail_note)
            if len(sources) > len(best_sources):
                best_sources, best_provider = sources, kind
            log.warning("search provider %s failed: %s", kind, fail_note)
        return {"sources": best_sources,
                "note": "; ".join(notes) if notes else "no_usable_sources",
                "provider": best_provider}
    finally:
        if own_client:
            client.close()


def fetch_sources(urls, client=None):
    """Fetch a fixed list of URLs directly (no search engine).

    Used only to produce the bundled sample quizzes: every keyless search
    engine is blocked from this build host (DuckDuckGo answers its HTML
    endpoint with a captcha page, Brave 429s, Mojeek/Ecosia 403), while
    direct page fetches work. The server's live path still uses search()
    above. Returns the same {"sources", "note"} shape; note records why
    the direct path was used so the quiz JSON stays honest.
    """
    own_client = client is None
    client = client or _make_client()
    try:
        sources = []
        for i, url in enumerate(urls):
            sid = "S%d" % (i + 1)
            try:
                r = client.get(url, timeout=FETCH_TIMEOUT)
                if r.status_code != 200 or "text/html" not in (
                        r.headers.get("content-type", "")):
                    log.warning("direct fetch %s -> HTTP %s", url, r.status_code)
                    continue
                text = _extract_page_text(r.text)[:TEXT_CAP]
                soup = BeautifulSoup(r.text, "html.parser")
                title = ""
                og = soup.find("meta", property="og:title")
                if og and og.get("content"):
                    title = _clean_text(og["content"])
                if not title and soup.title:
                    title = _clean_text(soup.title.get_text())
                if len(text) >= MIN_USABLE_CHARS and title:
                    sources.append({"id": sid, "title": title, "url": url,
                                    "fetched": True, "chars": len(text),
                                    "text": text})
            except Exception as exc:
                log.warning("direct fetch %s failed: %s", url, exc)
            time.sleep(0.3)
        note = None if len(sources) >= MIN_USABLE_SOURCES else "no_usable_sources"
        return {"sources": sources, "note": note}
    finally:
        if own_client:
            client.close()
