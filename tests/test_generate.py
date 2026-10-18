import copy
import random
from datetime import date

import pytest

from tests.conftest import q, short as make_short, FORMAT_SIZES
from fbc import generate
from fbc.models import BatchError
from fbc.weights import default_weights


def fake_gen_factory(bad_title=False, dup_question=None, wrong_ids=()):
    """Returns a fake generate_json that answers the three prompt kinds."""
    state = {"calls": 0}

    def gen(prompt, schema, grounded=False, **kw):
        state["calls"] += 1
        if grounded:  # verification
            ids = [line.split('"id": "')[1].split('"')[0] for line in prompt.splitlines() if '"id": "' in line]
            return [{"id": i, "correct": i not in wrong_ids, "answer": "x", "source_url": "https://src.example/" + i,
                     "note": "" if i not in wrong_ids else "answer disputed"} for i in ids]
        if "Family Quiz Night" in prompt:
            rounds = [{"name": n, "questions": [q("tmp", n=k) | {"question": f"Long {n} question number {k}?"} for k in range(7)]}
                      for n in generate.ROUND_NAMES]
            return {"rounds": rounds, "bonus": [q("tmp", n=2) | {"question": "Bonus question one?"},
                                                q("tmp", n=2) | {"question": "Bonus question two?"}],
                    "titles": ["Family Quiz Night #1", "Can You Beat Your Kids?", "Quiz Night"],
                    "description": "Family trivia quiz night.", "first_comment": "Your score?"}
        section = prompt.split("Write exactly these items")[1].split("Avoid these")[0]
        items = [l for l in section.splitlines() if l.startswith("- ")]
        out = []
        for i, line in enumerate(items):
            fmt = line[2:].split(",")[0].strip()
            s = make_short(i, fmt)
            for n, qq in enumerate(s["questions"]):
                qq["question"] = f"Unique {fmt} question {i} {n}?" if fmt != "would_you_rather" else "Would you rather...?"
            if bad_title and i == 0:
                s["titles"][0] = "Quiz for kids"
            if dup_question and i == 1 and fmt != "would_you_rather":
                s["questions"][0]["question"], s["questions"][0]["answer"] = dup_question
                if s["questions"][0]["options"]:
                    s["questions"][0]["options"][s["questions"][0]["answer_index"]] = dup_question[1]
            out.append({k: v for k, v in s.items() if k not in ("id", "hook", "hook_id")})
        return out
    return gen, state


def build(gen, used=()):
    return generate.build_batch("2026-W44", default_weights(), list(used), episode=1,
                                rng=random.Random(7), gen=gen, today=date(2026, 10, 23))


def test_builds_14_shorts_and_long():
    gen, state = fake_gen_factory()
    b = build(gen)
    assert len(b.shorts) == 14
    assert len(b.long.rounds) == 5 and all(len(r["questions"]) == 6 for r in b.long.rounds)
    assert {s.format for s in b.shorts} == set(FORMAT_SIZES)
    assert all(s.hook and s.hook_id for s in b.shorts)
    assert all(q.source_url for s in b.shorts if s.format != "would_you_rather" for q in s.questions)


def test_drops_copy_rule_failures():
    gen, _ = fake_gen_factory(bad_title=True)
    b = build(gen)
    assert all("for kids" not in t.lower() for s in b.shorts for t in s.titles)


def test_drops_duplicates():
    from fbc.dedupe import fingerprint
    dq = ("Which animal can sleep standing up?", "Horse")
    used = [{"question": dq[0], "answer": dq[1], "fingerprint": fingerprint(*dq), "date": "2026-10-01"}]
    gen, _ = fake_gen_factory(dup_question=dq)
    b = build(gen, used)
    assert all(q.question != dq[0] for s in b.shorts for q in s.questions)


def test_unverified_question_is_flagged_not_dropped():
    gen, _ = fake_gen_factory(wrong_ids=("q_2026w44_03_0",))
    b = build(gen)
    flagged = [q for s in b.shorts for q in s.questions if q.flags]
    assert any("unverified" in f for q in flagged for f in q.flags)
    assert all(not q.verified for q in flagged)


def test_too_few_survivors_raises():
    def gen(prompt, schema, grounded=False, **kw):
        if grounded or "Family Quiz Night" in prompt:
            return fake_gen_factory()[0](prompt, schema, grounded)
        return []
    with pytest.raises(BatchError):
        build(gen)


def test_tops_up_missing_shorts_with_one_more_call():
    good, _ = fake_gen_factory()
    calls = {"shorts": 0}

    def gen(prompt, schema, grounded=False, **kw):
        out = good(prompt, schema, grounded)
        if not grounded and "Family Quiz Night" not in prompt:
            calls["shorts"] += 1
            if calls["shorts"] == 1:   # first reply: half the items break the word limit
                for s in out[::2]:
                    s["questions"][0]["question"] = "word " * 30
        return out
    b = build(gen)
    assert len(b.shorts) == 14 and calls["shorts"] == 2


def test_too_few_survivors_error_lists_rejection_reasons():
    good, _ = fake_gen_factory()

    def gen(prompt, schema, grounded=False, **kw):
        out = good(prompt, schema, grounded)
        if not grounded and "Family Quiz Night" not in prompt:
            for s in out:
                s["questions"][0]["question"] = "word " * 30
        return out
    with pytest.raises(BatchError) as e:
        build(gen)
    text = "\n".join(e.value.problems)
    assert "only 0 of 14" in text and "words (max" in text


def test_difficulty_is_normalized_before_validation():
    good, _ = fake_gen_factory()

    def gen(prompt, schema, grounded=False, **kw):
        out = good(prompt, schema, grounded)
        if "Family Quiz Night" in prompt:
            for r in out["rounds"]:
                for qq in r["questions"]:
                    qq["difficulty"] = " Easy "
            out["bonus"][0]["difficulty"] = "HARD"
        return out
    b = build(gen)
    assert all(qq.difficulty == "easy" for r in b.long.rounds for qq in r["questions"])


def test_schema_constrains_difficulty():
    assert generate.Q_SCHEMA["properties"]["difficulty"]["enum"] == ["easy", "medium", "hard"]
