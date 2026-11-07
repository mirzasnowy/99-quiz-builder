"""Automated validation, design section 5 (V00-V10).

Pure functions, unit-tested in tests/test_validator.py. validate_questions()
takes the raw list of question dicts from the model plus the search sources
and returns (valid, failures) where failures maps question index -> list of
{"code", "message"}. A question is valid only if it passes every check.
"""
import json
import re
import string

BAD_OPTION_PATTERNS = [
    r"all of the above", r"none of the above", r"both a and b",
    r"semua (jawaban )?di atas", r"tidak ada (jawaban )?yang benar",
    r"semua benar", r"tidak ada yang benar", r"a dan b\b",
]

NORMS = str.maketrans("", "", string.punctuation)


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "").lower()
                  .translate(NORMS)).strip()


def _norm_tokens(text):
    return set(_norm(text).split())


def _jaccard(a, b):
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def parse_response(text):
    """V00: parse the model output as JSON with a questions array.

    Code fences are stripped first. Returns (questions, error) where
    error is None on success, else a short message.
    """
    if not text:
        return None, "empty output"
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        data = json.loads(t)
    except json.JSONDecodeError as exc:
        return None, "unparsable JSON: %s" % exc
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return None, "no questions array"
    return data["questions"], None


def validate_question(q, idx, earlier, sources_by_id, grounded):
    """Validate one question dict. earlier = list of already-valid
    question dicts (for the V08 duplicate check). sources_by_id maps
    source id -> source dict (with "text"). grounded = bool (grounded
    mode or not). Returns a list of {"code","message"} (empty = valid)."""
    fails = []

    def fail(code, message):
        fails.append({"code": code, "message": message})

    if not isinstance(q, dict):
        return [{"code": "V01", "message": "question is not an object"}]
    text = q.get("question")
    if not isinstance(text, str) or not (10 <= len(text.strip()) <= 250):
        fail("V01", "question must be a non-empty string of 10-250 chars")
    opts = q.get("options")
    if not isinstance(opts, dict) or set(opts.keys()) != {"A", "B", "C", "D"}:
        fail("V02", "options must have exactly the keys A, B, C, D")
        opts = opts if isinstance(opts, dict) else {}
    for k in ("A", "B", "C", "D"):
        v = opts.get(k)
        if not isinstance(v, str) or not v.strip() or len(v) > 140:
            fail("V02", "option %s must be a non-empty string of at most "
                        "140 chars" % k)
    vals = [str(opts.get(k, "")).strip().lower() for k in ("A", "B", "C", "D")]
    if len(set(vals)) != 4:
        fail("V03", "the four option texts must be distinct")
    if q.get("correct") not in ("A", "B", "C", "D"):
        fail("V04", "correct must be exactly one of A, B, C, D")
    for k in ("A", "B", "C", "D"):
        v = str(opts.get(k, "")).strip().lower()
        for pat in BAD_OPTION_PATTERNS:
            if re.search(pat, v):
                fail("V05", "option %s uses a forbidden pattern (%s)" % (k, pat))
                break
    expl = q.get("explanation")
    if not isinstance(expl, str) or not (20 <= len(expl.strip()) <= 400):
        fail("V06", "explanation must be 20-400 chars")
    if q.get("difficulty") not in ("easy", "medium", "hard"):
        fail("V07", "difficulty must be easy, medium or hard")
    toks = _norm_tokens(text)
    for prev in earlier:
        if _jaccard(toks, _norm_tokens(prev.get("question"))) >= 0.7:
            fail("V08", "duplicate of an earlier question (Jaccard >= 0.7)")
            break
    if grounded:
        sid = q.get("source_id")
        ev = q.get("evidence")
        if sid not in sources_by_id:
            fail("V09", "source_id %r is not one of the provided sources" % (sid,))
        elif not isinstance(ev, str) or len(ev.split()) < 6:
            fail("V09", "evidence must be a passage of 6+ words")
        else:
            hay = _norm(sources_by_id[sid].get("text", ""))
            if _norm(ev) not in hay:
                fail("V09", "evidence quote does not occur in source %s" % sid)
    else:
        if q.get("source_id") is not None or q.get("evidence") is not None:
            fail("V10", "ungrounded mode: source_id and evidence must be null")
    return fails


def validate_questions(questions, sources, grounded):
    """Validate a list of question dicts. Returns (valid, failures):
    valid = list of question dicts that passed every check (in order);
    failures = {index: [failure, ...]} for the ones that did not."""
    sources_by_id = {s["id"]: s for s in (sources or [])}
    valid, failures = [], {}
    for idx, q in enumerate(questions):
        fails = validate_question(q, idx, valid, sources_by_id, grounded)
        if fails:
            failures[idx] = fails
        else:
            valid.append(q)
    return valid, failures
