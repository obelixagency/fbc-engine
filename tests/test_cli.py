import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from fbc import cli
from fbc.store import ContentRepo
from tests.conftest import make_batch

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)   # Monday of 2026-W44, before the first slot


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    issues = []

    def gh(*a):
        if a[:2] == ("issue", "create"):
            issues.append(a)
        return "[]"
    r = ContentRepo(tmp_path, "o/fbc-content", run_gh=gh)
    r.issues = issues
    r.commit_and_push = lambda msg: None
    return r


def test_tick_dry_run_produces_within_48h_and_publishes_nothing(repo, capsys):
    repo.write_batch("2026-W44", json.dumps(make_batch()))
    n = cli.produce(repo, NOW, 48, dry_run=True)
    q = repo.queue()
    assert len(q) == 15
    within = [e for e in q if datetime.fromisoformat(e["slot_utc"]) <= NOW + timedelta(hours=48)]
    assert n == len(within) == 4   # Mon 07:00/16:00, Tue 07:00/16:00 ET
    assert cli.publish(repo, NOW, None, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "Which animal" not in out   # public logs never carry question text


def test_broken_merged_batch_opens_issue_with_every_problem(repo):
    b = make_batch()
    b["shorts"][0]["questions"][0]["options"].append("Cat")
    del b["shorts"][1]["questions"][0]["fun_fact"]
    repo.write_batch("2026-W44", json.dumps(b))
    produced = []
    cli.produce(repo, NOW, 48, dry_run=False, producer=lambda *a: produced.append(a))
    assert produced == [] and repo.queue() == []
    body = repo.issues[0][-1]
    assert "needs exactly 3 options" in body and "missing fun_fact" in body


def test_late_merge_skips_past_slots(repo):
    repo.write_batch("2026-W44", json.dumps(make_batch()))
    later = NOW + timedelta(days=2, hours=12)
    cli.intake(repo, later.date(), later)
    q = repo.queue()
    assert any(e["status"] == "skipped" for e in q)
    assert all(datetime.fromisoformat(e["slot_utc"]) >= later for e in q if e["status"] == "planned")


def test_quota_stop_keeps_going_next_tick(repo):
    repo.write_batch("2026-W44", json.dumps(make_batch()))
    from fbc.gemini import QuotaExceeded
    calls = []

    def producer(entry, batch, r, today):
        calls.append(entry["id"])
        if len(calls) == 2:
            raise QuotaExceeded("429")
        entry["status"] = "produced"
    cli.produce(repo, NOW, 48, dry_run=False, producer=producer)
    statuses = [e["status"] for e in repo.queue()][:4]
    assert statuses == ["produced", "planned", "planned", "planned"]


def test_pr_body_lists_every_question():
    from fbc.models import batch_from_obj
    b = batch_from_obj(make_batch())
    body = cli.pr_body(b)
    assert body.count("| s_2026w44_") == sum(len(s.questions) for s in b.shorts)
    assert "Merge to approve" in body


def test_generate_failure_opens_private_issue_with_problems(repo, monkeypatch):
    from fbc.models import BatchError

    def boom(*a, **k):
        raise BatchError(["only 9 of 14 Shorts survived", 's_x_01: question has 30 words (max 12)'])
    monkeypatch.setattr(cli, "build_batch", boom)
    with pytest.raises(BatchError):
        cli.cmd_generate(repo, "2026-W45", dry_run=True, today=NOW.date())
    title, body = repo.issues[0][-3], repo.issues[0][-1]
    assert "2026-W45" in title and "30 words" in body
