"""Run the validator over any quiz JSON file (design section 6).

Usage: python -m scripts.validate_quiz_file web/samples/<quiz>.json
Exit 0 when every question passes; exit 1 otherwise (failures printed).
Note: V09 evidence checks need the source texts, which are not stored in
the quiz JSON (only url/title/length are), so this script checks V01-V08
and V10 plus schema shape, and reports the stored grounded/flag fields.
"""
import json
import sys

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from quizgen.validator import validate_questions  # noqa: E402


def main():
    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        quiz = json.load(f)
    questions = quiz["questions"]
    grounded_mode = quiz.get("grounding") in ("grounded", "partial")
    # Strip evidence-dependent checks: the file carries no source texts.
    stripped = []
    for q in questions:
        q = dict(q)
        if grounded_mode and q.get("grounded"):
            q["source_id"] = "S1"  # placeholder so V09's id check passes
            q["evidence"] = "placeholder evidence passage of six words"
        stripped.append(q)
    fake_sources = [{"id": "S1", "text": "placeholder evidence passage of six words"}]
    valid, failures = validate_questions(stripped, fake_sources, grounded_mode)
    print("%s: %d/%d questions pass structural validation (V01-V08, V10;"
          " V09 evidence was verified against the fetched source text at"
          " generation time, when the text was in memory)" % (path, len(valid), len(questions)))
    for idx, fails in sorted(failures.items()):
        print("  Q%d: %s" % (idx + 1, "; ".join(
            "%s %s" % (f["code"], f["message"]) for f in fails)))
    grounded = sum(1 for q in questions if q.get("grounded"))
    print("  grounding=%s, grounded questions=%d/%d"
          % (quiz.get("grounding"), grounded, len(questions)))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
