"""Job API tests: POST /api/jobs returns 202, the worker runs the
pipeline with real stage progress, GET /api/jobs/{id} returns the quiz
when done, unknown ids 404, bad topics 400. The generator is stubbed
so no LLM call happens; the stub calls on_progress at the same stage
boundaries the real pipeline uses."""
import sys
import time

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from fastapi.testclient import TestClient  # noqa: E402

from server import main as server_main  # noqa: E402

FAKE_QUIZ = {
    "schema_version": 1, "id": "fake-quiz-20261107-0001",
    "topic": "How mortgages work", "language": "en",
    "generated_at": "2026-11-07T00:00:00Z", "grounding": "grounded",
    "search": {"provider": "wikipedia", "query": "How mortgages work",
               "sources": [], "note": None},
    "model": {"used": "fake", "providers_tried": 1},
    "validation": {"generated": 12, "repaired": 0, "dropped": 0,
                   "downgraded": 0, "warnings": []},
    "questions": [],
}


def _fake_generate(topic, num_questions=10, language="en", on_progress=None):
    for stage, pct in [("searching", 5), ("searching", 15),
                       ("drafting", 20), ("drafting", 65),
                       ("validating", 75), ("done", 100)]:
        if on_progress:
            on_progress(stage, pct)
        # Slow enough that the polling test samples every stage.
        time.sleep(0.12)
    quiz = dict(FAKE_QUIZ)
    quiz["topic"] = topic
    return quiz


def _client(monkeypatch):
    monkeypatch.setattr(server_main.generator, "generate_quiz", _fake_generate)
    return TestClient(server_main.app)


def test_job_flow_returns_quiz_with_real_stages(monkeypatch):
    client = _client(monkeypatch)
    r = client.post("/api/jobs", json={"topic": "How mortgages work",
                                       "num_questions": 5})
    assert r.status_code == 202, r.text
    body = r.json()
    job_id = body["job_id"]
    assert body["status"] == "searching"
    assert body["created_at"]

    seen_statuses = []
    quiz = None
    for _ in range(50):
        g = client.get("/api/jobs/%s" % job_id)
        assert g.status_code == 200, g.text
        data = g.json()
        seen_statuses.append(data["status"])
        assert 0 <= data["progress"] <= 100
        assert data["elapsed_s"] >= 0
        if data["status"] == "done":
            quiz = data["quiz"]
            break
        time.sleep(0.05)
    assert quiz is not None, "job never reached done: %s" % seen_statuses
    assert quiz["id"] == "fake-quiz-20261107-0001"
    # The worker really moved through the stages, in order.
    order = ["searching", "drafting", "validating", "done"]
    idx = [seen_statuses.index(s) for s in order]
    assert idx == sorted(idx), seen_statuses


def test_job_not_found(monkeypatch):
    client = _client(monkeypatch)
    r = client.get("/api/jobs/doesnotexist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "job_not_found"


def test_job_invalid_topic(monkeypatch):
    client = _client(monkeypatch)
    # Three spaces pass Pydantic's min_length=3 but strip to nothing,
    # so the endpoint's own check returns 400 invalid_topic.
    r = client.post("/api/jobs", json={"topic": "   ", "num_questions": 5})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_topic"


def test_job_error_maps_providers_unavailable(monkeypatch):
    def _failing(topic, num_questions=10, language="en", on_progress=None):
        if on_progress:
            on_progress("searching", 5)
        raise server_main.generator.ProvidersUnavailable("all failed")

    monkeypatch.setattr(server_main.generator, "generate_quiz", _failing)
    client = TestClient(server_main.app)
    r = client.post("/api/jobs", json={"topic": "How mortgages work"})
    job_id = r.json()["job_id"]
    data = None
    for _ in range(50):
        data = client.get("/api/jobs/%s" % job_id).json()
        if data["status"] == "error":
            break
        time.sleep(0.05)
    assert data["status"] == "error"
    assert data["error"]["code"] == "providers_unavailable"
