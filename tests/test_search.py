"""Search provider tests: DDG Lite parsing, Wikipedia parsing, the
provider chain (fall-through on failure), and the Brave opt-in."""
import os
import sys

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

import httpx  # noqa: E402

from quizgen import search as search_mod  # noqa: E402

DDG_LITE_HTML = """<html><body><table>
<tr><td class="result-link"><a class="result-link"
href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fsg-property&rut=abc"
>Singapore Property News</a></td></tr>
<tr><td class="result-snippet">Singapore home prices rose in the latest quarter.</td></tr>
<tr><td class="result-link"><a class="result-link"
href="https://example.com/mortgage-guide"
>Mortgage Guide 2026</a></td></tr>
<tr><td class="result-snippet">A mortgage is a loan secured by property, repaid monthly.</td></tr>
</table></body></html>"""

PAGE_HTML = ("<html><head><title>SG Property</title></head><body>"
             "<p>" + "Singapore property prices rose five percent. " * 12 + "</p>"
             "<p>" + "Demand from end users drove the gains. " * 12 + "</p>"
             "</body></html>")

WIKI_SEARCH_JSON = {"query": {"search": [
    {"title": "Mortgage", "pageid": 1},
    {"title": "Reverse mortgage", "pageid": 2},
    {"title": "Mortgage (disambiguation)", "pageid": 3}]}}
WIKI_EXTRACT_JSON = {"query": {"pages": {
    "1": {"title": "Mortgage",
          "extract": "A mortgage loan is a loan used by purchasers of real "
                     "property to raise funds. " * 12},
    "2": {"title": "Reverse mortgage",
          "extract": "A reverse mortgage lets older homeowners borrow "
                     "against their home equity. " * 12}}}}


def _transport(handler):
    return httpx.MockTransport(handler)


def test_ddg_lite_parses_links_and_snippets():
    """uddg redirect URLs are decoded to the real article URL."""
    def handler(request):
        assert request.url.host == "lite.duckduckgo.com"
        assert request.method == "GET"
        return httpx.Response(200, text=DDG_LITE_HTML)

    client = httpx.Client(transport=_transport(handler))
    results = search_mod._ddg_lite_results(client, "singapore property")
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/sg-property"
    assert results[0]["title"] == "Singapore Property News"
    assert "Singapore home prices rose" in results[0]["snippet"]
    assert results[1]["url"] == "https://example.com/mortgage-guide"


def test_ddg_lite_202_falls_through_to_wikipedia():
    """A 202 verification page from DDG Lite is a failure, not a crash;
    the chain falls through to Wikipedia and still grounds the quiz."""
    calls = {"n": 0}

    def handler(request):
        host = request.url.host
        if host == "lite.duckduckgo.com":
            calls["n"] += 1
            return httpx.Response(202, text="verification")
        if host == "en.wikipedia.org":
            if "list=search" in str(request.url):
                return httpx.Response(200, json=WIKI_SEARCH_JSON)
            return httpx.Response(200, json=WIKI_EXTRACT_JSON)
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=_transport(handler))
    out = search_mod.search("How mortgages work", client=client)
    assert out["note"] is None, out
    assert out["provider"] == "wikipedia"
    assert len(out["sources"]) == 2
    assert out["sources"][0]["url"].startswith("https://en.wikipedia.org/wiki/")
    assert out["sources"][0]["chars"] >= 200
    # DDG Lite retried once (RETRIES=1) before giving up.
    assert calls["n"] == 2


def test_wikipedia_skips_disambiguation_and_short_extracts():
    def handler(request):
        if "list=search" in str(request.url):
            return httpx.Response(200, json=WIKI_SEARCH_JSON)
        return httpx.Response(200, json=WIKI_EXTRACT_JSON)

    client = httpx.Client(transport=_transport(handler))
    results = search_mod._wikipedia_results(client, "mortgage")
    titles = [r["title"] for r in results]
    assert "Mortgage (disambiguation)" not in titles
    assert "Mortgage" in titles and "Reverse mortgage" in titles
    assert all(len(r["snippet"]) >= 200 for r in results)


def test_all_providers_fail_is_honest_ungrounded():
    def handler(request):
        return httpx.Response(403, text="blocked")

    client = httpx.Client(transport=_transport(handler))
    out = search_mod.search("some topic", client=client)
    assert out["sources"] == []
    assert out["note"] is not None
    assert "ddg_lite_blocked" in out["note"]
    assert "wikipedia_blocked" in out["note"]
    assert out["provider"] == "none"


def test_brave_skipped_without_key():
    """Without BRAVE_API_KEY the chain never calls Brave."""
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        if request.url.host == "lite.duckduckgo.com":
            return httpx.Response(202, text="verification")
        if request.url.host == "en.wikipedia.org":
            if "list=search" in str(request.url):
                return httpx.Response(200, json=WIKI_SEARCH_JSON)
            return httpx.Response(200, json=WIKI_EXTRACT_JSON)
        return httpx.Response(404, text="not found")

    os.environ.pop("BRAVE_API_KEY", None)
    client = httpx.Client(transport=_transport(handler))
    out = search_mod.search("How mortgages work", client=client)
    assert "api.search.brave.com" not in hosts
    assert out["provider"] == "wikipedia"


def test_brave_used_when_key_set():
    """With BRAVE_API_KEY the Brave API is tried first and wins."""
    def handler(request):
        host = request.url.host
        if host == "api.search.brave.com":
            assert request.headers.get("X-Subscription-Token") == "BSA-test"
            return httpx.Response(200, json={"web": {"results": [
                {"title": "SG Property News", "url": "https://example.com/a",
                 "description": "Singapore property news snippet."},
                {"title": "SG Prices", "url": "https://example.com/b",
                 "description": "Prices rose this quarter."}]}})
        if host == "example.com":
            return httpx.Response(200, text=PAGE_HTML,
                                  headers={"content-type": "text/html"})
        return httpx.Response(404, text="not found")

    os.environ["BRAVE_API_KEY"] = "BSA-test"
    try:
        client = httpx.Client(transport=_transport(handler))
        out = search_mod.search("singapore property", client=client)
    finally:
        os.environ.pop("BRAVE_API_KEY", None)
    assert out["note"] is None
    assert out["provider"] == "brave-api"
    assert len(out["sources"]) == 2
    assert out["sources"][0]["fetched"] is True
