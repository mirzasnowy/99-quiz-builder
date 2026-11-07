"""Model fallback chain for the 99 Quiz Builder (Section 2, Option C).

Copied from the Section 3 LKY chatbot's llm.py and adapted: the chain is
the same (OpenRouter free Gemma first, then Vikey Luna, then Vikey Flash),
but generate() takes temperature/max_tokens so the quiz pipeline can use
0.4/4000 for generation and 0.2/4000 for repair, and it returns the raw
text (a JSON object) instead of a chat answer. The UI model-choice
("prefer") machinery from Section 3 is dropped: the quiz pipeline always
uses the auto chain.

Tier 1: a free OpenRouter model (default google/gemma-4-26b-a4b-it:free).
Tier 2: Vikey openai/gpt-6-luna (paid, ~Rp 4/answer).
Tier 3: Vikey deepseek/deepseek-v4-flash (paid, ~Rp 5/answer).

One attempt per tier; the next tier is the retry. A tier that fails gets a
cooldown so later requests skip it instead of waiting on a call that will
fail. Paid calls are capped per UTC day (PAID_DAILY_CAP, default 300).

All configuration comes from env vars; nothing secret is in this file.
VIKEY_BASE_URL is used exactly as given + "/chat/completions" (never guessed).
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx

log = logging.getLogger("quizgen.llm")

OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODEL_DEFAULT = "google/gemma-4-26b-a4b-it:free"
VIKEY_MODEL_DEFAULT = "openai/gpt-6-luna"
VIKEY_MODEL_FALLBACK_DEFAULT = "deepseek/deepseek-v4-flash"

# Rp per 1 million tokens (in, out), from Vikey's price list.
PRICE_IDR = {
    "openai/gpt-6-luna": (720, 3600),
    "deepseek/deepseek-v4-flash": (1080, 1800),
}

CHAIN_MODELS = [OR_MODEL_DEFAULT, VIKEY_MODEL_DEFAULT,
                VIKEY_MODEL_FALLBACK_DEFAULT]

_client = httpx.Client()
_cooldown: dict[str, float] = {}
_paid = {"day": None, "n": 0}


class _Fail(Exception):
    """Internal: one tier failed with a classified error code."""


def set_client(client):
    """Replace the HTTP client (used by tests with a MockTransport)."""
    global _client
    _client = client


def reset_state():
    """Clear cooldowns and the paid-call counter (used by tests)."""
    _cooldown.clear()
    _paid.update(day=None, n=0)


def reset_cooldowns():
    """Clear only the per-tier cooldowns (used by the sample-generation CLI
    between retries, so a retry is not futile: cooldowns are meant for the
    live server's many requests over time, not for a one-off CLI retry).
    The paid-call counter is deliberately NOT reset here."""
    _cooldown.clear()


def _tier(name, provider, tier, url, key, model, timeout, headers=None):
    return {"name": name, "provider": provider, "tier": tier,
            "url": url, "key": key, "model": model, "timeout": timeout,
            "headers": headers or {}}


def _vikey_tier(name, env, default):
    base, vkey = os.getenv("VIKEY_BASE_URL"), os.getenv("VIKEY_API_KEY")
    if not (base and vkey):
        return None
    return _tier(name, "vikey", "paid",
                 base.rstrip("/") + "/chat/completions", vkey,
                 os.getenv(env, default), 90.0)


def _openrouter_tier():
    if not os.getenv("OPENROUTER_API_KEY"):
        return None
    return _tier("openrouter", "openrouter", "free", OR_URL,
                 os.environ["OPENROUTER_API_KEY"],
                 os.getenv("OPENROUTER_MODEL", OR_MODEL_DEFAULT), 60.0,
                 {"HTTP-Referer": "https://github.com/mirzasnowy/99-quiz-builder",
                  "X-Title": "99 Quiz Builder (99 Group AI Challenge, Section 2)"})


def build_chain():
    """The default tier list from env. A tier whose key is missing is skipped."""
    chain = []
    t = _openrouter_tier()
    if t:
        chain.append(t)
    for tier in (_vikey_tier("vikey-luna", "VIKEY_MODEL", VIKEY_MODEL_DEFAULT),
                 _vikey_tier("vikey-flash", "VIKEY_MODEL_FALLBACK",
                             VIKEY_MODEL_FALLBACK_DEFAULT)):
        if tier:
            chain.append(tier)
    return chain


def _paid_allowed():
    today = datetime.now(timezone.utc).date()
    if _paid["day"] != today:
        _paid.update(day=today, n=0)
    if _paid["n"] >= int(os.getenv("PAID_DAILY_CAP", "300")):
        return False
    _paid["n"] += 1
    return True


def _classify(status, retry_after):
    if status == 429:
        return "rate_limited", min(retry_after or 300, 900)
    if status in (401, 403):
        return "auth_error", 3600
    if status == 402:
        return "no_credit", 1800
    if status == 404:
        return "model_unavailable", 1800
    if status >= 500:
        return "server_error", 60
    return "bad_request", 60


def _retry_after(response):
    try:
        return int(response.headers.get("retry-after", ""))
    except (ValueError, TypeError):
        return None


def _prep(tier, messages):
    # Some Gemma endpoints reject role=system: merge system into the first
    # user message instead.
    if "gemma" in tier["model"].lower():
        if len(messages) >= 2:
            return [{"role": "user",
                     "content": messages[0]["content"] + "\n\n" + messages[1]["content"]}]
        return messages
    return messages


def _clean(text):
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def generate(messages, temperature=0.4, max_tokens=4000):
    """Run the fallback chain and return the raw text of the first tier
    that answers. Returns {"ok", "text", "provider", "model", "tier",
    "fallback_used", "truncated", "usage", "est_cost_idr", "attempts"}.
    A tier that returns unparsable or too-short text counts as failed and
    the chain moves on (the caller validates the JSON)."""
    attempts = []
    chain = build_chain()
    for i, tier in enumerate(chain):
        rec = {"provider": tier["provider"], "model": tier["model"],
               "tier": tier["tier"]}
        t0 = time.monotonic()
        if _cooldown.get(tier["name"], 0) > t0:
            attempts.append({**rec, "ok": False, "error": "skipped_cooldown"})
            continue
        if tier["tier"] == "paid" and not _paid_allowed():
            attempts.append({**rec, "ok": False, "error": "budget_cap"})
            continue
        err = "error"
        try:
            r = _client.post(
                tier["url"],
                headers={"Authorization": "Bearer " + tier["key"],
                         **tier["headers"]},
                json={"model": tier["model"], "messages": _prep(tier, messages),
                      "temperature": temperature, "max_tokens": max_tokens},
                timeout=httpx.Timeout(tier["timeout"], connect=5.0))
            if r.status_code != 200:
                err, cd = _classify(r.status_code, _retry_after(r))
                _cooldown[tier["name"]] = time.monotonic() + cd
                raise _Fail(err)
            data = r.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                # The model hit max_tokens mid-JSON; the text is cut off and
                # unparsable. Treat the tier as failed so the chain moves on
                # instead of handing a truncated blob to the validator.
                raise _Fail("truncated")
            text = _clean(choice["message"].get("content"))
            if len(text) < 20:
                raise _Fail("empty_response")
            usage = data.get("usage") or {}
            pin, pout = PRICE_IDR.get(tier["model"], (0, 0))
            cost = (round((usage.get("prompt_tokens", 0) * pin
                           + usage.get("completion_tokens", 0) * pout) / 1e6, 2)
                    if tier["tier"] == "paid" else 0.0)
            attempts.append({**rec, "ok": True,
                             "ms": int((time.monotonic() - t0) * 1000)})
            return {"ok": True, "text": text, "provider": tier["provider"],
                    "model": tier["model"], "tier": tier["tier"],
                    "fallback_used": i > 0,
                    "truncated": choice.get("finish_reason") == "length",
                    "usage": usage, "est_cost_idr": cost, "attempts": attempts}
        except _Fail as e:
            err = str(e)
        except httpx.TimeoutException:
            err = "timeout"
            _cooldown[tier["name"]] = time.monotonic() + 60
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            err = "error"
        attempts.append({**rec, "ok": False, "error": err,
                         "ms": int((time.monotonic() - t0) * 1000)})
    return {"ok": False, "text": None, "provider": None, "model": None,
            "tier": None, "fallback_used": False, "truncated": False,
            "usage": {}, "est_cost_idr": 0.0, "attempts": attempts}
