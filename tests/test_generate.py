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
            rounds = [{"name": n, "questions": [q("tmp", n=k) | {"question": f"Long {n} question number {k} call {state['calls']}?"} for k in range(7)]}
                      for n in generate.ROUND_NAMES]
            return {"rounds": rounds, "bonus": [q("tmp", n=2) | {"question": f"Bonus question one {state['calls']}?"},
                                                q("tmp", n=2) | {"question": f"Bonus question two {state['calls']}?"}],
                    "titles": ["Family Quiz Night #1", "Can You Beat Your Kids?", "Quiz Night"],
                    "description": "Family trivia quiz night.", "first_comment": "Your score?"}
        section = prompt.split("Write exactly these items")[1].split("Avoid these")[0]
        items = [l for l in section.splitlines() if l.startswith("- ")]
        out = []
        for i, line in enumerate(items):
            fmt = line[2:].split(",")[0].strip()
            s = make_short(i, fmt)
            for n, qq in enumerate(s["questions"]):
                qq["question"] = (f"Unique {fmt} question {state['calls']} {i} {n}?" if fmt != "would_you_rather"
                                  else "Would you rather...?")
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


def test_builds_a_full_week_of_shorts_and_long():
    gen, state = fake_gen_factory()
    b = build(gen)
    assert len(b.shorts) == generate.N_SHORTS == 28
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
    assert len(b.shorts) == generate.N_SHORTS and calls["shorts"] == 3


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
    assert "only 0 of 28" in text and "words (max" in text


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


def test_fact_check_quota_does_not_kill_the_batch():
    from fbc.gemini import QuotaExceeded
    good, _ = fake_gen_factory()

    def gen(prompt, schema, grounded=False, **kw):
        if grounded:
            raise QuotaExceeded("Gemini quota exceeded (429)", http=429)
        return good(prompt, schema, grounded)
    b = build(gen)
    qs = [q for s in b.shorts if s.format != "would_you_rather" for q in s.questions]
    assert len(b.shorts) == generate.N_SHORTS and qs and all(not q.verified for q in qs)
    assert all(any("fact-check quota" in f for f in q.flags) for q in qs)


def test_quota_error_names_the_exhausted_quota(monkeypatch):
    from fbc import gemini
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    class R:
        status_code = 429
        headers = {}
        text = "x"

        def json(self):
            return {"error": {"details": [{"violations": [{"quotaId": "SearchGroundingRequestsPerDay-FreeTier"}]}]}}
    import pytest as _p
    gemini._last_text_call = 0.0
    orig = gemini.requests.post
    gemini.requests.post = lambda *a, **k: R()
    try:
        with _p.raises(gemini.QuotaExceeded) as e:
            gemini._post("https://x/models/m:generateContent", {})
    finally:
        gemini.requests.post = orig
    assert "SearchGroundingRequestsPerDay" in str(e.value)


def test_batch_has_two_episodes_with_consecutive_numbers():
    gen, _ = fake_gen_factory()
    b = build(gen)
    assert [l.episode for l in b.longs] == [1, 2]
    assert len({l.id for l in b.longs}) == 2
    q1 = {q.question for q in b.longs[0].all_questions()}
    assert q1.isdisjoint({q.question for q in b.longs[1].all_questions()})
    from fbc.store import plan_queue
    q = plan_queue(b)
    assert [e["id"] for e in q if e["kind"] == "long"] == [l.id for l in b.longs]


def test_old_single_episode_batch_still_loads():
    import json as _j
    from fbc.models import parse_batch
    from tests.conftest import make_batch
    b = parse_batch(_j.dumps(make_batch()))
    assert len(b.longs) == 1 and b.long is b.longs[0]


def test_a_long_round_short_of_fresh_questions_gets_one_top_up_call():
    """Regression: W44 failed because 'Brain Teasers' had only 5 fresh riddles (classic ones repeat)."""
    gen, state = fake_gen_factory()
    calls = {"long": 0}

    def gen2(prompt, schema, grounded=False, **kw):
        out = gen(prompt, schema, grounded, **kw)
        if "Family Quiz Night" in prompt and not grounded:
            calls["long"] += 1
            if calls["long"] == 1:   # first answer: only 5 usable brain teasers
                out["rounds"][4]["questions"] = out["rounds"][4]["questions"][:5]
        return out
    b = build(gen2)
    assert all(len(r["questions"]) == 6 for l in b.longs for r in l.rounds)
    assert calls["long"] == len(generate.LONG_SLOTS_ET) + 1
