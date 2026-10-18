import json
import pytest
from fbc.models import parse_batch, BatchError


def test_round_trip(batch_dict):
    b = parse_batch(json.dumps(batch_dict))
    assert len(b.shorts) == 14 and len(b.long.rounds) == 5
    assert parse_batch(b.to_json()) == b


def problems(raw):
    with pytest.raises(BatchError) as e:
        parse_batch(raw)
    return e.value.problems


def test_trailing_comma(batch_dict):
    raw = json.dumps(batch_dict, indent=2)
    raw = raw.replace('"week": "2026-W44",', '"week": "2026-W44",,', 1)
    assert any(p.startswith("batch.json is not valid JSON (line 2") for p in problems(raw))


def test_four_options(batch_dict):
    batch_dict["shorts"][0]["questions"][0]["options"].append("Cat")
    assert "q_2026w44_00_0: needs exactly 3 options" in problems(json.dumps(batch_dict))


def test_long_question(batch_dict):
    batch_dict["shorts"][0]["questions"][0]["question"] = " ".join(["word"] * 14) + "?"
    assert "q_2026w44_00_0: question has 14 words (max 12)" in problems(json.dumps(batch_dict))


def test_missing_fun_fact(batch_dict):
    del batch_dict["shorts"][0]["questions"][1]["fun_fact"]
    assert "q_2026w44_00_1: missing fun_fact" in problems(json.dumps(batch_dict))


def test_reports_all_problems(batch_dict):
    batch_dict["shorts"][0]["questions"][0]["options"].append("Cat")
    del batch_dict["shorts"][0]["questions"][1]["fun_fact"]
    assert len(problems(json.dumps(batch_dict))) >= 2


def test_banned_copy_in_title(batch_dict):
    batch_dict["shorts"][2]["titles"][1] = "Easy quiz for kids"
    assert any("banned phrase" in p for p in problems(json.dumps(batch_dict)))


def test_wrong_question_count(batch_dict):
    batch_dict["shorts"][0]["questions"].pop()
    assert any("three_q needs 3 questions" in p for p in problems(json.dumps(batch_dict)))
