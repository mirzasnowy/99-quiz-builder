"""FastAPI server: GET /health, POST /api/generate, POST /api/jobs,
GET /api/jobs/{job_id}.

Rate limits (in-memory, reset on restart): 5 generations per IP per hour,
40 generations per day globally — protects the Vikey spend. CORS only
allows ALLOWED_ORIGINS. The synchronous request has a 110 s server
deadline; the frontend aborts at 120 s. The job flow exists for the same
reason: generation takes 20-90 s, so POST /api/jobs returns 202 at once
and the frontend polls GET /api/jobs/{id} for real stage progress
(searching -> drafting -> validating -> repairing? -> done). Progress is
reported at real pipeline stage boundaries by the generator's on_progress
callback, never a fake timer. Jobs live in memory (last 100, expire
after 10 minutes); a restart loses them, which is acceptable for a demo.
"""
import logging
import os
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from quizgen import generator
from quizgen.llm_client import CHAIN_MODELS
from quizgen.models import GenerateRequest

log = logging.getLogger("quizgen.server")

VERSION = "1.1.0"
PER_IP_PER_HOUR = 5
GLOBAL_PER_DAY = 40
JOB_TTL_S = 600  # jobs expire after 10 minutes
JOB_MAX = 100

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

# Job store: job_id -> record. Guarded by a lock; the worker thread writes
# progress, the request threads read it.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

_STAGE_LABELS = {
    "searching": "Searching the web for sources",
    "drafting": "Drafting questions",
    "validating": "Validating questions",
    "repairing": "Repairing failed questions",
    "done": "Done",
    "error": "Failed",
}


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


def _iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _prune_jobs():
    """Drop expired jobs and cap the store at JOB_MAX (oldest first)."""
    now = time.time()
    expired = [jid for jid, j in _jobs.items()
               if now - j["_created_ts"] > JOB_TTL_S]
    for jid in expired:
        del _jobs[jid]
    while len(_jobs) > JOB_MAX:
        oldest = min(_jobs, key=lambda jid: _jobs[jid]["_created_ts"])
        del _jobs[oldest]


def _job_public(job):
    """The API shape for a job (internal keys stripped)."""
    out = {
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "stage": _STAGE_LABELS.get(job["status"], job["status"]),
        "elapsed_s": round(time.time() - job["_created_ts"], 1),
    }
    if job.get("quiz") is not None:
        out["quiz"] = job["quiz"]
    if job.get("error") is not None:
        out["error"] = job["error"]
    return out


def _run_job(job_id, topic, num_questions, language):
    """Background worker: run the pipeline, updating the job record at
    real stage boundaries via the generator's on_progress callback."""
    def on_progress(stage, progress):
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None and job["status"] not in ("done", "error"):
                # "done" only sets progress: the status flips to done
                # atomically with the quiz attached below, so a reader never
                # sees status=done with no quiz yet.
                if stage != "done":
                    job["status"] = stage
                job["progress"] = int(progress)

    t0 = time.time()
    try:
        quiz = generator.generate_quiz(
            topic, num_questions, language, on_progress=on_progress)
    except generator.ProvidersUnavailable as exc:
        log.warning("job %s providers unavailable: %s", job_id, exc)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job.update(status="error", progress=100,
                           error={"code": "providers_unavailable",
                                  "message": "All models failed. Try again later."})
        return
    except generator.GenerationFailed as exc:
        log.warning("job %s generation failed: %s", job_id, exc)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job.update(status="error", progress=100,
                           error={"code": "generation_failed",
                                  "message": "Could not produce enough valid questions: %s" % exc})
        return
    except Exception as exc:  # never leak a stack trace to the client
        log.exception("job %s failed: %s", job_id, exc)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job.update(status="error", progress=100,
                           error={"code": "generation_failed",
                                  "message": "Generation failed."})
        return
    log.info("job %s generated quiz %s in %.1fs", job_id, quiz["id"],
             time.time() - t0)
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(status="done", progress=100, quiz=quiz)


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


@app.post("/api/jobs", status_code=202)
def api_create_job(req: GenerateRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not _rate_ok(ip):
        return _err("rate_limited",
                    "Too many generations. Try again later.", 429)
    topic = (req.topic or "").strip()
    if not (3 <= len(topic) <= 120):
        return _err("invalid_topic",
                    "Topic must be 3-120 characters.", 400)
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _prune_jobs()
        _jobs[job_id] = {
            "job_id": job_id, "status": "searching", "progress": 0,
            "quiz": None, "error": None, "_created_ts": time.time(),
        }
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, topic, req.num_questions, req.language),
        daemon=True)
    thread.start()
    log.info("job %s created for topic %r", job_id, topic)
    return {"job_id": job_id, "status": "searching",
            "created_at": _iso_now()}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    with _jobs_lock:
        _prune_jobs()
        job = _jobs.get(job_id)
        if job is None:
            return _err("job_not_found",
                        "No job with that id (jobs expire after 10 minutes).",
                        404)
        return _job_public(job)
