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

1. **Search.** The server searches DuckDuckGo for the topic, takes up to 5
   results, and fetches the top 3 pages (6-second timeout each). Pages with
   at least 200 characters of text count as usable sources. Fewer than 2
   usable sources means the quiz is generated in *ungrounded* mode and is
   flagged as such — never silently presented as grounded.
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
exactly one model writes each answer, in this order — free
`google/gemma-4-26b-a4b-it:free` on OpenRouter, then `openai/gpt-6-luna` on
Vikey, then `deepseek/deepseek-v4-flash` on Vikey. Each tier has a cooldown
after failures (429: Retry-After or 300s, capped at 900s; 401/403: 1 hour;
402/404: 30 minutes; 5xx/timeout: 60s) and a paid daily cap (300 answers)
bounds the worst-case bill. See `docs/architecture.md` for the full detail.

## API

- `GET /health` — `{"status": "ok", "version": "1.0.0", "chain": [...]}`.
- `POST /api/generate` — body `{"topic": "...", "num_questions": 10,
  "language": "en"}` (num_questions 5-10, language en or id). Returns
  `{"quiz": {...}}`. The full quiz JSON schema is documented in
  `docs/architecture.md`.
- Errors: `400 invalid_topic`, `429 rate_limited` (5 per IP per hour, 40 per
  day globally, in-memory), `502 generation_failed` (fewer than 5 valid
  questions after repair), `503 providers_unavailable` (all model tiers
  failed).

## Key decisions

- **Free model first, paid as fallback.** The free Gemma tier on OpenRouter
  costs nothing, so most quizzes are free. The paid fallbacks exist only so
  a rate limit or outage on the free tier does not leave the user with
  nothing. Measured cost per generated quiz is a few hundred rupiah at most
  when the free tier answers; the paid fallback is about Rp 4-5 per quiz
  generation (one LLM call).
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
in-app quiz editor, streaming progress updates, a second search provider,
an LLM-as-judge, numeric cross-checks of figures in questions, persistent
rate limits, database/accounts, PDF/Word export, analytics, a separate
samples endpoint, an admin dashboard, a Cloudflare Worker backend, and an
Ollama fallback. None of these serve the score or the play experience in
the time available.

## What went wrong / what I would change

- **Every keyless search engine is blocked from this build host.**
  DuckDuckGo's HTML endpoint answers with a captcha page, Brave Search
  returns 429 + captcha, Mojeek and Ecosia return 403, and Bing is slow
  (one query timed out) and returns generic results (Wikipedia, travel
  guides) instead of current market news. The live server keeps DuckDuckGo
  search as designed — it may work from Render's IPs — but the two bundled
  sample quizzes could not be generated through the search step on this
  machine. They were instead grounded by fetching four reputable article
  pages directly per topic (the URLs are recorded in each quiz's
  `search.sources`, with `search.provider` set to `direct-fetch` and a note
  explaining the block), then run through the same generate / validate /
  repair pipeline. If I had more time I would add a second search provider
  (for example a Wikipedia API fallback for general topics) so live
  generation degrades gracefully instead of going ungrounded.
- **Bing's redirect URLs need resolving.** Bing result links are
  `bing.com/ck/a?...` redirects, not the article URL, so a Bing fallback
  would need an extra resolution step before fetching.
- **The free model tier rate-limits.** The OpenRouter free Gemma tier
  returned 429 during testing, which is exactly why the fallback chain
  exists — the paid Vikey tiers answered immediately. Nothing to fix, but
  it confirms the chain order matters more than the free tier's raw speed.
- **Cold start on the free Render tier.** The backend sleeps after ~15
  minutes idle and takes about 50 seconds to wake. The frontend hides this
  with a "Waking generator..." chip and retries the health check every 5
  seconds for 60 seconds, and the bundled samples always work, but a first
  visitor still waits. A paid tier would fix it; for this demo the samples
  cover it.

## Repo layout

- `quizgen/` — the generator package: `llm_client.py` (model fallback
  chain), `search.py` (DuckDuckGo search + page fetch), `validator.py`
  (V00-V10 checks), `generator.py` (pipeline), `models.py` (schemas),
  `prompts.py` (prompt loading).
- `prompts/` — the three prompts, verbatim: `system.txt`,
  `user_template.txt`, `repair_template.txt`.
- `server/main.py` — FastAPI app: `/health`, `POST /api/generate`,
  rate limits, CORS.
- `web/` — static frontend (index.html, style.css, app.js, confetti.js,
  config.js) and `web/samples/` — the two bundled sample quizzes (JSON +
  plain-text twins).
- `scripts/generate_sample.py` — CLI that runs the pipeline for a topic and
  writes a sample quiz; `scripts/validate_quiz_file.py` — structural
  validation of a quiz JSON file.
- `tests/test_validator.py` — 11 tests, one per validation code.
- `docs/architecture.md` — full technical description; `docs/sample-run-log.txt`
  — the real console output of the sample generation runs.
- `render.yaml` — Render service definition; `requirements.txt` — Python
  dependencies.
