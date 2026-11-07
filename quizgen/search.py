"""Web search grounding (design section 5, search step).

POST https://html.duckduckgo.com/html/ (form field q), parse up to 5
results (title, URL, snippet) with BeautifulSoup. Fetch the top 3 pages
(6 s timeout each, HTML only, identifying User-Agent), extract paragraph
text, cap each at 1800 chars. Remaining results contribute their snippets.
A source is usable with 200+ chars. Fewer than 2 usable sources means
ungrounded mode, with search.note set. All search failures are caught and
degrade to ungrounded mode — never an exception to the caller.
"""
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("quizgen.search")

DDG_URL = "https://html.duckduckgo.com/html/"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
      "99-quiz-builder/1.0 (+https://github.com/mirzasnowy/99-quiz-builder)")
SEARCH_TIMEOUT = 15.0
FETCH_TIMEOUT = 6.0
MAX_RESULTS = 5
FETCH_TOP = 3
TEXT_CAP = 1800
MIN_USABLE_CHARS = 200
MIN_USABLE_SOURCES = 2


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


def search(query, client=None):
    """Return {"sources": [...], "note": None | str}.

    Each source: {"id": "S1", "title": str, "url": str, "fetched": bool,
    "chars": int, "text": str}. Only usable sources (>=200 chars) are
    returned; note is set when search failed or too few sources were
    usable (the caller then runs ungrounded mode).
    """
    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)
    try:
        return _search(client, query)
    finally:
        if own_client:
            client.close()


def _search(client, query):
    try:
        r = client.post(DDG_URL, data={"q": query}, timeout=SEARCH_TIMEOUT)
        if r.status_code != 200:
            log.warning("DDG search HTTP %s", r.status_code)
            return {"sources": [], "note": "search_blocked"}
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for a in soup.select("a.result__a")[:MAX_RESULTS]:
            url = (a.get("href") or "").strip()
            title = _clean_text(a.get_text())
            snippet = ""
            parent = a.find_parent("div", class_="result")
            if parent is not None:
                sn = parent.select_one(".result__snippet")
                if sn is not None:
                    snippet = _clean_text(sn.get_text())
            if url and title:
                results.append({"title": title, "url": url, "snippet": snippet})
        if not results:
            return {"sources": [], "note": "no_usable_sources"}
    except Exception as exc:  # network error, parse error, captcha page, ...
        log.warning("DDG search failed: %s", exc)
        return {"sources": [], "note": "search_blocked"}

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
    if len(sources) < MIN_USABLE_SOURCES:
        return {"sources": sources, "note": "no_usable_sources"}
    return {"sources": sources, "note": None}


def fetch_sources(urls, client=None):
    """Fetch a fixed list of URLs directly (no search engine).

    Used only to produce the bundled sample quizzes: every keyless search
    engine is blocked from this build host (DuckDuckGo answers its HTML
    endpoint with a captcha page, Brave 429s, Mojeek/Ecosia 403), while
    direct page fetches work. The server's live path still uses search()
    above (DuckDuckGo may work from Render's IPs — tested at deploy time).
    Returns the same {"sources", "note"} shape; note records why the
    direct path was used so the quiz JSON stays honest.
    """
    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)
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
