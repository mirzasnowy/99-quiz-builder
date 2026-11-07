"""FastAPI server (design section 2): GET /health, POST /api/generate.

Rate limits (in-memory, reset on restart): 5 generations per IP per hour,
40 generations per day globally — protects the Vikey spend. CORS only
allows ALLOWED_ORIGINS. The request is synchronous with a 110 s server
deadline; the frontend aborts at 120 s.
"""
import logging
import os
import time
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quizgen import generator
from quizgen.llm_client import CHAIN_MODELS
from quizgen.models import GenerateRequest

log = logging.getLogger("quizgen.server")

VERSION = "1.0.0"
PER_IP_PER_HOUR = 5
GLOBAL_PER_DAY = 40

app = FastAPI(title="99 Quiz Builder", version=VERSION)

ALLOWED = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_ip_hits: dict[str, list[float]] = defaultdict(list)
_day = {"date": None, "n": 0}


def _rate_ok(ip):
    import datetime
    today = datetime.date.today().isoformat()
    if _day["date"] != today:
        _day.update(date=today, n=0)
    now = time.time()
    hits = [t for t in _ip_hits[ip] if now - t < 3600]
    _ip_hits[ip] = hits
    if len(hits) >= PER_IP_PER_HOUR or _day["n"] >= GLOBAL_PER_DAY:
        return False
    hits.append(now)
    _day["n"] += 1
    return True


def _err(code, message, status):
    return JSONResponse({"error": {"code": code, "message": message}},
                        status_code=status)


@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION, "chain": CHAIN_MODELS}


@app.post("/api/generate")
def api_generate(req: GenerateRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not _rate_ok(ip):
        return _err("rate_limited",
                    "Too many generations. Try again later.", 429)
    topic = (req.topic or "").strip()
    if not (3 <= len(topic) <= 120):
        return _err("invalid_topic",
                    "Topic must be 3-120 characters.", 400)
    t0 = time.time()
    try:
        quiz = generator.generate_quiz(topic, req.num_questions, req.language)
    except generator.ProvidersUnavailable as exc:
        log.warning("providers unavailable: %s", exc)
        return _err("providers_unavailable",
                    "All models failed. Try again later.", 503)
    except generator.GenerationFailed as exc:
        log.warning("generation failed: %s", exc)
        return _err("generation_failed",
                    "Could not produce enough valid questions: %s" % exc, 502)
    except Exception as exc:  # never leak a stack trace to the client
        log.exception("generate failed: %s", exc)
        return _err("generation_failed", "Generation failed.", 502)
    log.info("generated quiz %s in %.1fs", quiz["id"], time.time() - t0)
    return {"quiz": quiz}
