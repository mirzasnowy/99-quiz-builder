"""Load and fill the three prompt files in prompts/.

Placeholders in {braces} are filled by the caller. The system prompt is
sent as-is; the user and repair templates take topic, language, mode,
n_generate / failures_json, and the sources block.
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(BASE, "prompts")


def _load(name):
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


def system_prompt():
    return _load("system.txt")


def user_prompt(topic, language, mode, n_generate, sources_block, today):
    return _load("user_template.txt").format(
        topic=topic, language=language, mode=mode,
        n_generate=n_generate, sources_block=sources_block, today=today)


def repair_prompt(topic, language, mode, sources_block, failures_json):
    return _load("repair_template.txt").format(
        topic=topic, language=language, mode=mode,
        sources_block=sources_block, failures_json=failures_json)


def build_sources_block(sources):
    """Grounded-mode <sources> block: titles only, no URLs (the model
    never writes URLs), each text capped at ~1800 chars, total ~7000."""
    if not sources:
        return "NO SOURCES AVAILABLE. Follow the UNGROUNDED MODE rules."
    parts = ["<sources>"]
    total = 0
    for s in sources:
        text = (s.get("text") or "")[:1800]
        if total + len(text) > 7000 and parts != ["<sources>"]:
            break
        total += len(text)
        parts.append("[{sid}] Title: {title}\nText: {text}".format(
            sid=s["id"], title=s.get("title", ""), text=text))
    parts.append("</sources>")
    return "\n\n".join(parts)
