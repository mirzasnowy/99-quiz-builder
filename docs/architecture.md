# Architecture — Employee Quiz Builder

## Overview

One Python package powers the CLI and the live API; one static page is the
frontend. No framework, no vector database, no hosted dependency besides the
two model providers and the free hosts (Render for the API, Cloudflare Pages
for the frontend).

```
User ──► Cloudflare Pages (static web/) ──► Render (FastAPI server/main.py)
                                            │
                                            ├─► quizgen/search.py ──► DuckDuckGo HTML
                                            ├─► quizgen/llm_client.py ──► Vikey Luna / Flash
                                            │                          (Vikey-only since v1.2.0)
                                            └─► quizgen/validator.py (V00-V10, pure code)
```

## Quiz JSON schema

```json
{
  "schema_version": 1,
  "id": "jakarta-property-market-20261107-a3f9",
  "topic": "Property Market updates in Jakarta",
  "language": "en",
  "generated_at": "2026-11-07T06:30:00Z",
  "grounding": "grounded",
  "search": {
    "provider": "duckduckgo-html",
    "query": "Property Market updates in Jakarta",
    "sources": [
      {"id": "S1", "title": "...", "url": "https://...",
       "fetched": true, "chars": 1800}
    ],
    "note": null
  },
  "model": {"used": "openai/gpt-6-luna",
            "providers_tried": ["vikey"]},
  "validation": {"generated": 12, "repaired": 1, "dropped": 2,
                 "downgraded": 0, "warnings": []},
  "questions": [
    {"id": "q1", "question": "...",
     "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
     "correct": "B",
     "explanation": "...",
     "difficulty": "medium",
     "grounded": true,
     "source_id": "S1",
     "source_url": "https://...",
     "source_title": "...",
     "evidence": "six to twenty words quoted verbatim",
     "flags": []}
  ]
}
```

- `grounding` is `grounded` (every question cites a source), `partial`
  (some questions ungrounded), or `ungrounded` (no usable sources at all).
- `search.note` explains why grounding failed when it did, for example
  `search_blocked` or `no_usable_sources`. For the two bundled samples the
  provider is `direct-fetch` with note
  `duckduckgo_blocked_from_build_host` — see "Search" below.
- `validation.warnings` lists non-fatal notes, for example
  `only_7_questions` when fewer than 10 valid questions survived.

## Search

`quizgen/search.py` POSTs the topic to `https://html.duckduckgo.com/html/`,
parses up to 5 results with BeautifulSoup, fetches the top 3 pages (6-second
timeout each, HTML only, identifying User-Agent), and extracts paragraph
text, capped at 1,800 characters per source. A source counts as usable at
200+ characters. Fewer than 2 usable sources means ungrounded mode.

Known limitation: every keyless search engine is blocked from the build
host used to produce the bundled samples (DuckDuckGo answers with a captcha
page, Brave Search 429s, Mojeek and Ecosia 403, Bing is slow and returns
generic results). The live server keeps DuckDuckGo search as designed — it
may work from Render's IPs, and this is tested at deploy time. The bundled
samples were grounded by fetching four reputable article pages directly per
topic (`quizgen/search.py::fetch_sources`), with the provenance recorded
honestly in each quiz's `search` block (`provider: direct-fetch`).

## Model chain

`quizgen/llm_client.py` (copied from the Section 3 LKY chatbot's `src/llm.py`
and adapted). The chain is a reliability fallback: exactly one model writes
each answer, in this order:

1. Vikey `openai/gpt-6-luna` (paid, ~Rp 720 in / Rp 3,600 out per 1M tokens).
2. Vikey `deepseek/deepseek-v4-flash` (paid, ~Rp 1,080 in / Rp 1,800 out
   per 1M tokens — the price list in the code; the Vikey dashboard lists
   Rp 1,350 in / Rp 5,400 out for the v4.1 variant, so the estimate is a
   lower bound).

The chain is Vikey-only since v1.2.0 (2026-11-07): the OpenRouter free
Gemma tier was removed because its upstream shared pool rate-limits
constantly (HTTP 429), which made whole generations fail whenever Vikey
also hiccupped.

Per-tier cooldowns: 429 → Retry-After or 300s (capped 900s); 401/403 →
3600s; 402 → 1800s; 404 → 1800s; 5xx/timeout → 60s. A response cut off by
the token limit (`finish_reason: length`) counts as a failed tier, not as a
usable answer. Paid calls are capped at 300 per UTC day
(`PAID_DAILY_CAP`). Tier timeout: Vikey 90s (a 12-question
JSON is a long generation; the first run timed out at the old 40s limit).
Generation uses temperature 0.4, repair 0.2, max_tokens 8,000 (the design
said 4,000, but a 12-question JSON does not fit in 4,000 tokens).

## Validation (V00–V10)

`quizgen/validator.py` — pure code, no LLM:

- V00: the model output parses as JSON and has a `questions` array (code
  fences stripped first).
- V01: question 10–250 characters.
- V02: options exactly A–D, each non-empty and at most 140 characters.
- V03: the four options are distinct.
- V04: `correct` is one of A–D.
- V05: no "all of the above", "none of the above", or "both A and B" (English
  and Indonesian).
- V06: explanation 20–400 characters.
- V07: difficulty is easy, medium, or hard.
- V08: no duplicate questions (Jaccard similarity below 0.7).
- V09 (grounded): `source_id` exists and the `evidence` quote (6+ words)
  occurs verbatim in the cited source text after normalisation.
- V10 (ungrounded): `source_id` and `evidence` are null.

## Repair loop

`quizgen/generator.py`: generate 12 questions, validate, then at most 2
repair rounds. Each repair call names every failing question by failure
code. A question whose citation still fails (V09) is downgraded to
ungrounded with flag `unverified_citation`; any other still-failing question
is dropped. If more than N valid questions survive, keep the first N. 5 to
N-1 valid → return with a warning. Fewer than 5 → 502. Options are then
shuffled with a seed from the quiz id, the correct letter is remapped, and
V02/V04 are re-checked. At most 3 successful LLM calls per quiz.

## API

`server/main.py` (FastAPI):

- `GET /health` → `{"status": "ok", "version": "1.0.0", "chain": [...]}`.
- `POST /api/generate` — body `{"topic", "num_questions" (5-10, default
  10), "language" ("en"|"id")}`. Returns `{"quiz": {...}}` synchronously
  (110s server deadline; the frontend aborts at 120s).
- Errors: `400 invalid_topic`, `429 rate_limited` (5 per IP per hour, 40
  per day globally, in-memory), `502 generation_failed` (fewer than 5 valid
  questions after repair), `503 providers_unavailable` (all model tiers
  failed).
- CORS: only the origins in `ALLOWED_ORIGINS` (Pages URL +
  `http://localhost:8080`).

## Frontend

`web/` — vanilla HTML/CSS/JS, no framework, no CDN. Screens: Home (topic
box, language select, Generate, sample cards, backend status chip), Loading,
Quiz, Results, Review. Game rules: 25s timer per question (bar shrinks, red
+ pulses in the last 5s; timeout = wrong, 0 points, streak resets); points
`100 + round(50*secondsLeft/25) + 10*min(streak-1,5)` (max 150 on Q1, 200
from Q6, 1,850 for 10 questions); correct = green pulse + floating
"+points", wrong = shake + correct option highlighted; explanation + source
link, then Next (no auto-advance); streak flame counter; animated progress
bar; results count-up 1.2s; grades Gold/Silver/Bronze/Keep Learning;
confetti at 80%+ (vanilla canvas, `web/confetti.js`); review mode (your
answer vs correct, explanation, source link, grounded badge); personal best
per quiz id in localStorage; `prefers-reduced-motion` disables animations.
Max-width 560px, no horizontal overflow at 390px, tap targets 48px.

The backend status chip: on load, `GET /health` retries every 5s for 60s
("Waking generator..." → "Live generator online" or "Offline: bundled
samples only"). With no `API_BASE` configured the chip goes straight to
"Offline: bundled samples only" — the samples play with no backend.

## Deployment

- Frontend: Cloudflare Pages, project `99-quiz-builder`, output `web/`.
- Backend: Render free web service from this repo, build
  `pip install -r requirements.txt`, start
  `uvicorn server.main:app --host 0.0.0.0 --port $PORT`. Env vars
  `VIKEY_API_KEY`, `VIKEY_BASE_URL`,
  `ALLOWED_ORIGINS` are set in the Render dashboard (never committed).
- Render free tier sleeps after ~15 minutes idle (~50s cold start) and
  shares 750 instance-hours/month across services.

## What is NOT built

Sound, achievements, 50/50 lifelines, leaderboard, multi-day streaks,
shareable result image, dark-mode toggle, UI language toggle, in-app quiz
editor, streaming progress, second search provider, LLM-as-judge, numeric
cross-checks, persistent rate limits, database/accounts, PDF/Word export,
analytics, separate samples endpoint, admin dashboard, Cloudflare Worker
backend, Ollama fallback. See README for the reasons.
