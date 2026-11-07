"""Validator tests (design section 6): about 10 cases, one per V-code."""
import sys

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from quizgen.validator import parse_response, validate_questions  # noqa: E402

SOURCES = [{"id": "S1", "text": "Jakarta property prices rose five percent in 2025 amid strong demand"},
           {"id": "S2", "text": "A property agent needs negotiation skills and local market knowledge"}]


def good_q(**over):
    q = {"question": "Which factor most directly lifts Jakarta apartment prices?",
         "options": {"A": "Rising land costs", "B": "More billboards",
                     "C": "Longer weekends", "D": "Cheaper paint"},
         "correct": "A",
         "explanation": "Land is the scarcest input in central Jakarta, so its cost drives prices.",
         "difficulty": "medium", "source_id": "S1",
         "evidence": "Jakarta property prices rose five percent in 2025"}
    q.update(over)
    return q


def test_v00_parses_json_with_fences():
    qs, err = parse_response('```json\n{"questions": []}\n```')
    assert qs == [] and err is None
    qs, err = parse_response('not json')
    assert qs is None and err is not None


def test_v01_question_length():
    _, f = validate_questions([good_q(question="short")], SOURCES, True)
    assert f and f[0][0]["code"] == "V01"


def test_v02_options_shape():
    _, f = validate_questions([good_q(options={"A": "x"})], SOURCES, True)
    assert any(x["code"] == "V02" for x in f[0])


def test_v03_distinct_options():
    _, f = validate_questions([good_q(options={"A": "same", "B": "same",
                                              "C": "c", "D": "d"})], SOURCES, True)
    assert any(x["code"] == "V03" for x in f[0])


def test_v04_correct_letter():
    _, f = validate_questions([good_q(correct="E")], SOURCES, True)
    assert any(x["code"] == "V04" for x in f[0])


def test_v05_no_all_of_the_above():
    _, f = validate_questions([good_q(options={"A": "All of the above", "B": "b",
                                              "C": "c", "D": "d"})], SOURCES, True)
    assert any(x["code"] == "V05" for x in f[0])


def test_v06_explanation_length():
    _, f = validate_questions([good_q(explanation="too short")], SOURCES, True)
    assert any(x["code"] == "V06" for x in f[0])


def test_v07_difficulty():
    _, f = validate_questions([good_q(difficulty="extreme")], SOURCES, True)
    assert any(x["code"] == "V07" for x in f[0])


def test_v08_duplicate_question():
    q2 = good_q(question="Which factor most directly lifts Jakarta apartment prices?")
    _, f = validate_questions([good_q(), q2], SOURCES, True)
    assert any(x["code"] == "V08" for x in f[1])


def test_v09_evidence_must_occur_in_source():
    _, f = validate_questions([good_q(evidence="words never in any source text here")],
                              SOURCES, True)
    assert any(x["code"] == "V09" for x in f[0])
    v, f = validate_questions([good_q()], SOURCES, True)
    assert len(v) == 1 and not f


def test_v10_ungrounded_mode():
    v, f = validate_questions([good_q(source_id=None, evidence=None)],
                              SOURCES, False)
    assert len(v) == 1 and not f
    _, f = validate_questions([good_q()], SOURCES, False)
    assert any(x["code"] == "V10" for x in f[0])
