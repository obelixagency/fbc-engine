import json
import subprocess
from datetime import date

import pytest

from fbc.store import ContentRepo, plan_queue
from fbc.models import batch_from_obj
from tests.conftest import make_batch


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    calls = []
    r = ContentRepo(tmp_path, "obelixagency/fbc-content", run_gh=lambda *a: calls.append(a) or "")
    r.calls = calls
    return r


def test_queue_round_trip(repo):
    assert repo.queue() == []
    repo.save_queue([{"id": "x", "status": "planned"}])
    assert repo.queue() == [{"id": "x", "status": "planned"}]


def test_used_and_clips(repo):
    repo.append_used([{"question": "Q?", "answer": "A", "fingerprint": "f", "date": "2026-10-01"}])
    assert repo.used()[0]["answer"] == "A"
    repo.add_clip_ids([5, 6], date(2026, 10, 1))
    assert repo.recent_clip_ids(30, date(2026, 10, 20)) == {5, 6}
    assert repo.recent_clip_ids(30, date(2026, 11, 20)) == set()


def test_batch_and_merge_state(repo):
    raw = json.dumps(make_batch())
    assert not repo.is_merged("2026-W44")
    repo.write_batch("2026-W44", raw)
    assert repo.is_merged("2026-W44")
    assert len(repo.load_batch("2026-W44").shorts) == 14


def test_plan_queue_assigns_15_slots():
    b = batch_from_obj(make_batch())
    q = plan_queue(b)
    assert len(q) == 15 and sum(e["kind"] == "long" for e in q) == 1
    assert all(e["status"] == "planned" and set(e["platforms"]) == {"youtube", "instagram", "facebook"} for e in q)
    assert len({e["slot_utc"] for e in q}) == 15


def test_issue_not_duplicated(repo):
    repo.run_gh = lambda *a: (repo.calls.append(a), '[{"title": "late: s1"}]' if a[1] == "list" else "")[1]
    repo.open_issue("late: s1", "body")
    assert not any(c[1] == "create" for c in repo.calls)
