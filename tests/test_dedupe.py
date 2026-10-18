from datetime import date, timedelta
from fbc.dedupe import normalize, fingerprint, is_duplicate

TODAY = date(2026, 10, 18)


def used(q, a, days_ago):
    return {"question": q, "answer": a, "fingerprint": fingerprint(q, a),
            "date": (TODAY - timedelta(days=days_ago)).isoformat()}


def test_normalize():
    assert normalize("  Which ANIMAL, can sleep?! ") == "which animal can sleep"


def test_punctuation_case_duplicate():
    u = [used("Which animal can sleep standing up?", "Horse", 10)]
    assert is_duplicate("which animal can sleep standing up", "horse", u, today=TODAY)


def test_old_entry_not_duplicate():
    u = [used("Which animal can sleep standing up?", "Horse", 181)]
    assert not is_duplicate("Which animal can sleep standing up?", "Horse", u, today=TODAY)


def test_jaccard_duplicate_same_answer():
    u = [used("Which animal can sleep standing up?", "Horse", 5)]
    assert is_duplicate("What animal is able to sleep standing up?", "Horse", u, today=TODAY)


def test_similar_question_different_answer_not_duplicate():
    u = [used("Which animal can sleep standing up?", "Horse", 5)]
    assert not is_duplicate("Which animal can sleep standing up?", "Flamingo", u, today=TODAY)
