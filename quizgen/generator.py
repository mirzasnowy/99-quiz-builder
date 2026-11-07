"""Generation pipeline (design sections 0, 5): web search, then ONE
over-generating LLM call (12 questions for a 10-question quiz), then
automated validation, then at most 2 repair rounds, then a seeded option
shuffle, then return 10.

Grounding: the model returns a source_id plus a verbatim evidence quote.
The server resolves the URL and checks that the quote really occurs in the
fetched text (V09). The model never writes URLs, so it cannot invent them.
"""
import hashlib
import json
import logging
import random
import re
from datetime import datetime, timezone

from . import llm_client, prompts as prompt_mod, search as search_mod
from .validator import parse_response, validate_questions

log = logging.getLogger("quizgen.generator")

N_GENERATE = 12
MAX_REPAIR_ROUNDS = 2
OVER_GENERATE_MIN = 5  # below this many valid questions -> 502


class GenerationFailed(Exception):
    """Fewer than 5 valid questions after repair."""


class ProvidersUnavailable(Exception):
    """All three LLM tiers failed."""


def _slug(topic):
    s = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:40]
    return s or "quiz"


def _quiz_id(topic):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    digest = hashlib.sha256(
        (topic + datetime.now(timezone.utc).isoformat()).encode()).hexdigest()[:4]
    return "%s-%s-%s" % (_slug(topic), stamp, digest)


def _call_llm(system, user, temperature):
    # max_tokens is 8000, not the design's 4000: a 12-question JSON with
    # explanations and evidence quotes does not fit in 4000 tokens and came
    # back cut off mid-string (V00 "Unterminated string") on the first run.
    res = llm_client.generate(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=temperature, max_tokens=8000)
    if not res["ok"]:
        raise ProvidersUnavailable(
            "all providers failed: %s" % (res["attempts"],))
    return res


def _parse_questions(text):
    questions, err = parse_response(text)
    if err:
        raise ProvidersUnavailable("model output failed V00: %s" % err)
    return questions


def _finalise_question(raw, idx, sources_by_id, downgraded=False):
    """Attach server-resolved fields to a validated raw question."""
    sid = raw.get("source_id")
    src = sources_by_id.get(sid) if sid else None
    grounded = bool(src) and isinstance(raw.get("evidence"), str) and not downgraded
    if downgraded:
        flags = ["unverified_citation"]
    elif grounded:
        flags = []
    else:
        flags = ["ungrounded"]
    return {
        "id": "q%d" % (idx + 1),
        "question": raw["question"].strip(),
        "options": {k: raw["options"][k].strip() for k in ("A", "B", "C", "D")},
        "correct": raw["correct"],
        "explanation": raw["explanation"].strip(),
        "difficulty": raw["difficulty"],
        "grounded": grounded,
        "source_id": sid if grounded else None,
        "source_url": src["url"] if grounded else None,
        "source_title": src["title"] if grounded else None,
        "evidence": raw.get("evidence") if grounded else None,
        "flags": flags,
    }


def _shuffle_options(questions, seed):
    """Seeded shuffle of every question's options (seed = quiz id),
    remapping `correct`. Removes the LLM's habit of favouring one letter."""
    rng = random.Random(seed)
    out = []
    for q in questions:
        keys = ["A", "B", "C", "D"]
        values = [q["options"][k] for k in keys]
        order = list(range(4))
        rng.shuffle(order)
        new_opts = {}
        new_correct = None
        for new_i, old_i in enumerate(order):
            letter = keys[new_i]
            new_opts[letter] = values[old_i]
            if keys[old_i] == q["correct"]:
                new_correct = letter
        q = dict(q)
        q["options"] = new_opts
        q["correct"] = new_correct
        out.append(q)
    return out


def _emit(on_progress, stage, progress):
    """Report a real pipeline stage to the caller's progress callback.
    Never let a progress callback break generation."""
    if on_progress is None:
        return
    try:
        on_progress(stage, progress)
    except Exception as exc:  # noqa: BLE001 - progress is best-effort
        log.warning("on_progress %s/%s failed: %s", stage, progress, exc)


def generate_quiz(topic, num_questions=10, language="en", sources_override=None,
                  on_progress=None):
    """Run the full pipeline. Returns the quiz dict (schema section 3).
    Raises GenerationFailed (too few valid questions) or
    ProvidersUnavailable (all LLM tiers failed).

    sources_override: optional list of URLs. When given, the search step
    is skipped and those pages are fetched directly (see search.fetch_sources).
    Used only to produce the bundled sample quizzes, because every keyless
    search engine is blocked from this build host. The quiz JSON records
    provider "direct-fetch" and the note "duckduckgo_blocked_from_build_host"
    so the provenance stays honest.

    on_progress: optional callable(stage, progress) called at real stage
    boundaries only — searching 5/15, drafting 20/65, validating 75,
    repairing 85 (only when a repair round actually runs), done 100.
    Never a fake timer.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    quiz_id = _quiz_id(topic)

    # 1. Search (degrades to ungrounded mode on any failure), unless the
    # caller supplied source URLs directly (sample generation only).
    _emit(on_progress, "searching", 5)
    if sources_override:
        result = search_mod.fetch_sources(sources_override)
        provider = "direct-fetch"
        search_note = result["note"] or "duckduckgo_blocked_from_build_host"
    else:
        result = search_mod.search(topic)
        provider = result["provider"]
        search_note = result["note"]
    _emit(on_progress, "searching", 15)
    sources = result["sources"]
    grounded_mode = result["note"] is None
    sources_by_id = {s["id"]: s for s in sources}
    search_block = {
        "provider": provider,
        "query": topic,
        "sources": [{"id": s["id"], "title": s["title"], "url": s["url"],
                     "fetched": s["fetched"], "chars": s["chars"]}
                    for s in sources],
        "note": search_note,
    }

    # 2. Generate (over-generate: 12 for a 10-question quiz).
    system = prompt_mod.system_prompt()
    mode = "GROUNDED" if grounded_mode else "UNGROUNDED"
    sources_block = prompt_mod.build_sources_block(sources) if grounded_mode else \
        "NO SOURCES AVAILABLE. Follow the UNGROUNDED MODE rules."
    user = prompt_mod.user_prompt(topic, language, mode, N_GENERATE,
                                  sources_block, today)
    _emit(on_progress, "drafting", 20)
    res = _call_llm(system, user, temperature=0.4)
    _emit(on_progress, "drafting", 65)
    model_used = res["model"]
    providers_tried = len(res["attempts"])
    questions = _parse_questions(res["text"])

    # 3. Validate + repair (max 2 rounds).
    _emit(on_progress, "validating", 75)
    valid, failures = validate_questions(questions, sources, grounded_mode)
    generated = len(questions)
    repaired = 0
    for _round in range(MAX_REPAIR_ROUNDS):
        if not failures:
            break
        _emit(on_progress, "repairing", 85)
        failed = [{"index": idx, "question": questions[idx],
                   "failures": failures[idx]} for idx in sorted(failures)]
        repair_user = prompt_mod.repair_prompt(
            topic, language, mode, sources_block,
            json.dumps(failed, ensure_ascii=False))
        res = _call_llm(system, repair_user, temperature=0.2)
        fixed = _parse_questions(res["text"])
        for j, idx in enumerate(sorted(failures)):
            if j < len(fixed):
                questions[idx] = fixed[j]
        repaired += len(failed)
        valid, failures = validate_questions(questions, sources, grounded_mode)

    # 4. Post-repair disposition: keep valid ones; V09-only failures are
    # downgraded (kept, never passed off as grounded); anything else drops.
    kept = []  # (raw question dict, downgraded bool)
    dropped = 0
    for idx, q in enumerate(questions):
        if idx not in failures:
            kept.append((q, False))
        elif {f["code"] for f in failures[idx]} == {"V09"}:
            kept.append((q, True))
        else:
            dropped += 1
    if len(kept) < OVER_GENERATE_MIN:
        raise GenerationFailed(
            "only %d valid questions after repair" % len(kept))
    kept = kept[:num_questions]
    downgraded = sum(1 for _q, dg in kept if dg)
    finalised = [_finalise_question(q, i, sources_by_id, downgraded=dg)
                 for i, (q, dg) in enumerate(kept)]

    # 5. Seeded shuffle, then re-check V02/V04 only (options intact,
    # correct still one of A-D). A shuffle must never break a question;
    # if it did, fall back to the pre-shuffle order.
    shuffled = _shuffle_options(finalised, quiz_id)
    shuffle_ok = all(
        set(q["options"].keys()) == {"A", "B", "C", "D"}
        and q["correct"] in ("A", "B", "C", "D")
        and all(isinstance(v, str) and v.strip() for v in q["options"].values())
        for q in shuffled)
    if not shuffle_ok:
        log.warning("post-shuffle check failed; keeping pre-shuffle order")
    else:
        finalised = shuffled

    grounded_count = sum(1 for q in finalised if q["grounded"])
    grounding = ("grounded" if grounded_count == len(finalised)
                 else "partial" if grounded_count > 0 else "ungrounded")
    warnings = []
    if len(finalised) < num_questions:
        warnings.append("only %d of %d valid" % (len(finalised), num_questions))
    _emit(on_progress, "done", 100)
    return {
        "schema_version": 1,
        "id": quiz_id,
        "topic": topic,
        "language": language,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grounding": grounding,
        "search": search_block,
        "model": {"used": model_used, "providers_tried": providers_tried},
        "validation": {"generated": generated, "repaired": repaired,
                       "dropped": dropped, "downgraded": downgraded,
                       "warnings": warnings},
        "questions": finalised,
    }
