# Employee Quiz Builder

An automated quiz builder for 99 Group employees: type any topic and get a
10-question multiple-choice quiz, grounded in web sources, validated by code,
and playable as a game. Built for Section 2, Option C of the 99 Group AI
Aptitude Challenge.

## Demo

- Play it: https://99-quiz-builder.pages.dev/
- Backend API: https://quiz-builder-o0r5.onrender.com
- Sample quiz 1 (Jakarta property market): https://99-quiz-builder.pages.dev/samples/jakarta-property-market.json
- Sample quiz 2 (top skills of a property agent in 2025): https://99-quiz-builder.pages.dev/samples/top-skills-property-agent-2025.json

## How to play

Open the demo, pick a sample quiz, or type your own topic (for example
"Property Market updates in Jakarta") and press Generate. A new quiz takes
20-60 seconds: the server searches the web for sources, drafts questions
with a language model, then validates every question by code before showing
it. Each question has a 25-second timer. Answer fast and keep a streak for
more points: 100 base + up to 50 for speed + up to 50 for a streak (max
1,850 for 10 questions). At the end you get a grade — Gold "Property Pro"
(90%+), Silver "Market Savvy" (70-89%), Bronze "Getting There" (50-69%), or
"Keep Learning" (below 50%) — and a review screen with the correct answer,
explanation, and source link for every question. Your personal best per quiz
is saved in your browser.

## How generation works

1. **Search.** The server runs a provider chain and takes the first one
   that yields at least 2 usable sources (200+ characters of text each):
   Brave Search API first, but only when `BRAVE_API_KEY` is set (free tier:
   2,000 queries/month — see "Key decisions"); then DuckDuckGo Lite; then
   the Wikipedia API (search + lead-section extracts). For DuckDuckGo Lite
   the real article URL is decoded out of the `uddg` redirect parameter;
   for Wikipedia the source is the article URL. Pages with at least 200
   characters of text count as usable sources. Fewer than 2 usable sources
   from every provider means the quiz is generated in *ungrounded* mode and
   is flagged as such — never silently presented as grounded. The quiz JSON
   records the provider and the failure notes honestly (`search.provider`,
   `search.note`). Grounding quality depends on the topic: general topics
   ("How mortgages work") ground well on Wikipedia; current-news topics
   ("Property Market updates in Singapore") ground on Wikipedia's
   background articles (Economy of Singapore, Public housing), not on
   today's news — DuckDuckGo Lite would give current news, but it is
   blocked from Render's servers (see "What went wrong").
2. **Generate.** One language-model call drafts 12 questions for a
   10-question quiz (temperature 0.4, max 8,000 tokens). The prompt forces
   JSON-only output and a strict rule: every grounded question must cite a
   source id and quote 6-20 words verbatim from that source. The model never
   writes URLs — the server resolves them from the search results. (The
   design said 4,000 tokens, but a 12-question JSON does not fit in 4,000 —
   the first run came back cut off mid-string. A response cut off by the
   token limit is treated as a failed tier and the chain moves on.)
3. **Validate.** Every question is checked by code (checks V01-V10): question
   length, exactly 4 distinct options, correct letter in A-D, no "all of the
   above" style options, explanation length, difficulty label, no duplicate
   questions, and — for grounded questions — the quoted evidence really
   occurs in the cited source text.
4. **Repair.** Each failing question is named by failure code in one repair
   call (temperature 0.2), at most 2 rounds. A question whose citation still
   fails is downgraded to ungrounded (flagged `unverified_citation`); other
   failures drop the question. Fewer than 5 valid questions after repair is
   a 502 error, not a quiz.
5. **Shuffle.** Options are shuffled with a seed from the quiz id, the
   correct letter is remapped, and the structural checks run again, so the
   correct answer is not always in the same position.

The model chain is a reliability fallback, not a multi-model pipeline:
exactly one model writes each answer, in this order — `openai/gpt-6-luna`
on Vikey, then `deepseek/deepseek-v4-flash` on Vikey. The chain is
Vikey-only (changed 2026-11-07): the OpenRouter free Gemma tier was
dropped because its upstream shared pool rate-limits constantly (HTTP 429),
which made whole generations fail whenever Vikey also hiccupped. Each tier
has a cooldown after failures (429: Retry-After or 300s, capped at 900s;
401/403: 1 hour; 402/404: 30 minutes; 5xx/timeout: 60s) and a paid daily
cap (300 answers) bounds the worst-case bill. See `docs/architecture.md`
for the full detail.

## API

- `GET /health` — `{"status": "ok", "version": "1.2.0", "chain": [...]}`.
- `POST /api/generate` — body `{"topic": "...", "num_questions": 10,
  "language": "en"}` (num_questions 5-10, language en or id). Returns
  `{"quiz": {...}}`. The full quiz JSON schema is documented in
  `docs/architecture.md`.
- `POST /api/jobs` — same body and validation as `/api/generate`. Returns
  `202 {"job_id": "...", "status": "searching", "created_at": "..."}` at
  once; the quiz takes 20-90 s to build.
- `GET /api/jobs/{job_id}` — `{"job_id", "status", "progress", "stage",
  "elapsed_s", "quiz"? , "error"?}`. Status is one of `searching`,
  `drafting`, `validating`, `repairing`, `done`, `error`; `stage` is the
  human label ("Searching the web for sources", ...); `progress` is 0-100
  and moves only at real pipeline stage boundaries (never a fake timer).
  `quiz` appears when status is `done`; `error` when status is `error`.
  Unknown id returns `404 job_not_found`. Jobs live in memory (last 100,
  expire after 10 minutes).
- Errors: `400 invalid_topic`, `429 rate_limited` (5 per IP per hour, 40 per
  day globally, in-memory), `502 generation_failed` (fewer than 5 valid
  questions after repair), `503 providers_unavailable` (all model tiers
  failed).

## Key decisions

- **Vikey-only, no free tier.** The chain is GPT-6 Luna first, DeepSeek V4
  Flash as the fallback, both on Vikey. The OpenRouter free Gemma tier was
  removed 2026-11-07 because its upstream shared pool rate-limits
  constantly (HTTP 429 on nearly every attempt), which made whole
  generations fail whenever Vikey also hiccupped. A paid quiz generation
  is one LLM call: roughly Rp 9 (DeepSeek V4 Flash) to Rp 15 (GPT-6 Luna)
  at the listed per-token prices for a typical input (~2,700 tokens of
  prompt + sources) and output (~3,500 tokens of JSON) — an estimate from
  the price list, not a measured bill. The paid daily cap (300 calls)
  bounds the worst case.
- **No vector database, no framework.** Retrieval is plain web search plus
  code validation. A vector database would add a hosted dependency and cost
  for a demo whose corpus is the live web, not a fixed document set.
- **Validation by code, not by a second LLM.** A second LLM judging the
  first one doubles the cost and the failure modes; the structural and
  citation checks (V01-V10) are deterministic and cheap, and the evidence
  quote check is exact string matching after normalisation.
- **Static frontend, live backend optional.** The two bundled sample quizzes
  play with no backend at all, so the demo never shows a dead page. When the
  backend is awake, any topic can be generated live.
- **Vanilla JavaScript, no CDN.** The whole frontend is plain HTML/CSS/JS in
  `web/`. No framework, no external script or font, so it loads fast and
  cannot break because a third-party host is down.

## What is NOT built (cuts for time)

Sound effects, achievement badges, 50/50 lifelines, leaderboard, multi-day
streaks, shareable result image, dark-mode toggle, UI language toggle,
in-app quiz editor, an LLM-as-judge, numeric cross-checks of figures in
questions, persistent rate limits, database/accounts, PDF/Word export,
analytics, a separate samples endpoint, an admin dashboard, a Cloudflare
Worker backend, and an Ollama fallback. None of these serve the score or
the play experience in the time available. (A second search provider and
job progress reporting were originally in this list; both are now built —
see "How generation works" and the `/api/jobs` endpoints above.)

## What went wrong / what I would change

- **DuckDuckGo is blocked from everywhere this runs — and the fix took
  three providers, not one.** DuckDuckGo's HTML endpoint answers with a
  captcha page from the build host and times out from Render's servers.
  DuckDuckGo Lite (the lighter endpoint) is flaky too: it returned 10 real
  news results once for "Property Market updates in Singapore", but usually
  answers 202 (the verification page) from the build host and times out
  from Render. SearXNG public instances are no better (searx.be returns
  HTML instead of JSON, search.bus-hit.me disconnects, searxng.org 404s),
  and the earlier probes showed Brave web 429 + captcha, Mojeek/Ecosia 403,
  Bing slow and generic. The fix that shipped is a provider chain in
  `quizgen/search.py`: Brave Search API first when `BRAVE_API_KEY` is set
  (free tier, 2,000 queries/month — the one reliable current-news source),
  then DuckDuckGo Lite, then the Wikipedia API. Wikipedia is what grounds
  live generation today: it is free, needs no key, and works from Render.
  Two sharp edges found while wiring it up: (1) httpx's default
  `Accept-Encoding: gzip, deflate, br` makes Wikimedia's edge return 403 —
  the search client now sends `Accept-Encoding: gzip` only; (2) a
  full-article Wikipedia extract comes back 0 chars for some pages
  ("Mortgage", "Reverse mortgage" both returned empty), while the lead
  section (`exintro=True`) reliably returns ~2,000 chars — so extracts are
  lead-only, fetched for up to 5 titles. The two bundled sample quizzes
  were grounded by fetching four reputable article pages directly per topic
  (the URLs are recorded in each quiz's `search.sources`, with
  `search.provider` set to `direct-fetch` and a note explaining the block),
  then run through the same generate / validate / repair pipeline. The
  remaining honest limit: live generation grounds general topics well on
  Wikipedia, but current-news topics ground on Wikipedia's background
  articles, not on today's news — for current news, set `BRAVE_API_KEY`
  (sign up at https://brave.com/search/api/, key from
  https://api-dashboard.search.brave.com/, add it as an env var in the
  Render dashboard; no code change needed, the server picks it up on the
  next deploy). One caveat from Brave's own users: new signups in 2026 have
  reportedly been asked for a card even on the free tier, so treat the free
  tier as "free, but signup may ask for a card".
- **Bing's redirect URLs need resolving.** Bing result links are
  `bing.com/ck/a?...` redirects, not the article URL, so a Bing fallback
  would need an extra resolution step before fetching.
- **The free model tier rate-limits — so it was removed.** The OpenRouter
  free Gemma tier returned 429 on nearly every sample-generation attempt,
  and on 2026-11-07 it was rate-limited at the same moment both Vikey
  tiers returned empty responses, so a live job failed with "All models
  failed" even though nothing was misconfigured. The fix was to drop the
  free tier entirely: the chain is now Vikey-only (GPT-6 Luna, then
  DeepSeek V4 Flash), and a retry of the same generation succeeded at
  once. Reliability beats free when the free tier is the thing failing.
- **The paid tiers are slow and flaky on long generations.** A 12-question
  JSON is a long output: GPT-6 Luna timed out twice at the old 40-second
  limit and once at 90 seconds, and DeepSeek V4 Flash timed out once and
  returned a 502 once. The fixes were raising the Vikey per-tier timeout
  from 40s to 90s and raising max_tokens from 4,000 to 8,000 — a 4,000-token
  limit cut the JSON off mid-string. A
  response cut off by the token limit is now treated as a failed tier so
  the chain moves on instead of handing a truncated blob to the validator.
- **A literal JSON example in the repair prompt broke Python's `.format()`.**
  The repair prompt template contained `{"questions":[...]}` as an example,
  and `.format()` read it as a placeholder, raising `KeyError: '"questions"'`
  every time the repair loop ran. The Jakarta sample never hit it (all 12
  questions passed first time, so no repair ran); the skills sample failed
  three times in a row before the cause was found. The fix is doubling the
  braces (`{{"questions":[...]}}`) in the template. The system prompt's
  JSON example is safe because it is loaded as-is, never formatted.
- **Two of the four skills sources refused to load.** Both Indeed pages
  (in.indeed.com, au.indeed.com) returned 403 Forbidden, so the skills
  sample is grounded in the two remaining sources (Engel & Völkers, IQI
  Global) — still enough for grounded mode, and every question still cites
  a source. If a topic's sources all refuse, the quiz degrades to
  ungrounded mode and says so.
- **Cold start on the free Render tier.** The backend sleeps after ~15
  minutes idle and takes about 50 seconds to wake. The frontend hides this
  with a "Waking generator..." chip and retries the health check every 5
  seconds for 60 seconds, and the bundled samples always work, but a first
  visitor still waits. A paid tier would fix it; for this demo the samples
  cover it.

## Repo layout

- `quizgen/` — the generator package: `llm_client.py` (model fallback
  chain), `search.py` (provider chain: Brave API opt-in, DuckDuckGo Lite,
  Wikipedia + page fetch), `validator.py` (V00-V10 checks), `generator.py`
  (pipeline, with an `on_progress` callback at real stage boundaries),
  `models.py` (schemas), `prompts.py` (prompt loading).
- `prompts/` — the three prompts, verbatim: `system.txt`,
  `user_template.txt`, `repair_template.txt`.
- `server/main.py` — FastAPI app: `/health`, `POST /api/generate`,
  `POST /api/jobs` + `GET /api/jobs/{job_id}` (async generation with real
  stage progress), rate limits, CORS.
- `web/` — static frontend (index.html, style.css, app.js, confetti.js,
  config.js) and `web/samples/` — the two bundled sample quizzes (JSON +
  plain-text twins).
- `scripts/generate_sample.py` — CLI that runs the pipeline for a topic and
  writes a sample quiz; `scripts/validate_quiz_file.py` — structural
  validation of a quiz JSON file.
- `tests/test_validator.py` — 11 tests, one per validation code;
  `tests/test_search.py` — provider parsing + chain fall-through + Brave
  opt-in; `tests/test_jobs.py` — job creation, polling to done, 404/400,
  error mapping (21 tests total, all passing).
- `docs/architecture.md` — full technical description; `docs/sample-run-log.txt`
  — the real console output of the sample generation runs.
- `render.yaml` — Render service definition; `requirements.txt` — Python
  dependencies.
