"""CLI: generate one sample quiz and write the JSON + a readable .txt twin.

Usage:
  python -m scripts.generate_sample --topic "Property Market updates in Jakarta" \
      --out web/samples/jakarta-property-market.json [--language en]

Keys come from the environment (never from a file): OPENROUTER_API_KEY,
VIKEY_API_KEY, VIKEY_BASE_URL. Retries the whole pipeline up to 2 times
until 10 valid questions exist (design section 5).
"""
import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quizgen import generator, llm_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("generate_sample")


def quiz_to_text(quiz):
    lines = ["%s" % quiz["topic"], "",
             "Generated: %s | Grounding: %s | Model: %s"
             % (quiz["generated_at"], quiz["grounding"],
                quiz["model"]["used"]), ""]
    for i, q in enumerate(quiz["questions"], 1):
        lines.append("Q%d. %s" % (i, q["question"]))
        for letter in ("A", "B", "C", "D"):
            lines.append("   %s. %s" % (letter, q["options"][letter]))
        lines.append("   Correct: %s" % q["correct"])
        lines.append("   Explanation: %s" % q["explanation"])
        if q.get("source_url"):
            lines.append("   Source: %s (%s)"
                         % (q["source_title"], q["source_url"]))
        if q["flags"]:
            lines.append("   Flags: %s" % ", ".join(q["flags"]))
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--num-questions", type=int, default=10)
    ap.add_argument("--language", default="en")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--sources-file",
                    help="JSON file with a list of source URLs to fetch "
                         "directly instead of searching (sample generation "
                         "only; the quiz JSON records provider direct-fetch)")
    args = ap.parse_args()
    sources_override = None
    if args.sources_file:
        with open(args.sources_file, encoding="utf-8") as f:
            sources_override = json.load(f)
    last_exc = None
    for attempt in range(args.retries + 1):
        if attempt > 0:
            # Cooldowns are per-process and persist across retries, which
            # would make a retry futile (every tier skipped). Clear them so
            # the retry actually re-tries; the paid-call counter is kept.
            # A short pause gives a rate-limited free tier a chance to reset.
            llm_client.reset_cooldowns()
            log.warning("retry %d: cooldowns cleared, waiting 30s", attempt)
            time.sleep(30)
        try:
            quiz = generator.generate_quiz(
                args.topic, args.num_questions, args.language,
                sources_override=sources_override)
            break
        except Exception as exc:
            last_exc = exc
            logging.warning("attempt %d failed: %s", attempt + 1, exc)
    else:
        raise SystemExit("generation failed after %d attempts: %s"
                         % (args.retries + 1, last_exc))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(quiz, f, ensure_ascii=False, indent=2)
    txt_path = os.path.splitext(args.out)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(quiz_to_text(quiz))
    logging.info("wrote %s (%d questions, grounding=%s, model=%s, "
                 "validation=%s)", args.out, len(quiz["questions"]),
                 quiz["grounding"], quiz["model"]["used"],
                 quiz["validation"])
    logging.info("wrote %s", txt_path)


if __name__ == "__main__":
    main()
