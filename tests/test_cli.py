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
    assert n == len(within) == 8   # Mon + Tue, 4 slots a day ET
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


def test_private_youtube_upload_opens_one_issue(repo):
    entry = {"id": "s_x_00", "platforms": {"youtube": {"status": "published", "note": "private_pending_audit: ..."}}}
    cli._warn_if_youtube_private(repo, entry)
    assert "Private" in repo.issues[0][-3]
    cli._warn_if_youtube_private(repo, {"id": "s_x_01", "platforms": {"youtube": {"status": "published"}}})
    assert len(repo.issues) == 1


def test_any_generate_failure_is_reported_and_repeats_become_comments(tmp_path, monkeypatch):
    calls = []

    def gh(*a):
        calls.append(a)
        if a[:2] == ("issue", "list"):
            return json.dumps([{"title": "[dry run] Batch 2026-W45 generation failed", "number": 7}]) if len(calls) > 2 else "[]"
        return ""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    r = ContentRepo(tmp_path, "o/fbc-content", run_gh=gh)
    from fbc.gemini import GeminiError
    monkeypatch.setattr(cli, "build_batch", lambda *a, **k: (_ for _ in ()).throw(GeminiError("bad", http=400, reason="INVALID_ARGUMENT")))
    for _ in range(2):
        with pytest.raises(GeminiError):
            cli.cmd_generate(r, "2026-W45", dry_run=True, today=NOW.date())
    assert calls[1][:2] == ("issue", "create") and "INVALID_ARGUMENT" in calls[1][-1]
    assert calls[3][:3] == ("issue", "comment", "7")


def test_refresh_covers_sets_hook_frame_on_published_shorts(repo, monkeypatch, tmp_path):
    from fbc.publish import youtube
    import fbc.render as render
    repo.save_queue([{"id": "s1", "kind": "short", "week": "2026-W43", "asset": "s1.mp4",
                      "platforms": {"youtube": {"status": "published", "ref": "YT1"}}},
                     {"id": "s2", "kind": "short", "week": "2026-W43", "asset": "s2.mp4",
                      "platforms": {"youtube": {"status": "pending", "ref": None}}}])
    monkeypatch.setattr(youtube, "access_token", lambda: "t")
    sent = []
    monkeypatch.setattr(youtube, "set_thumbnail", lambda vid, png, token=None: sent.append(vid))
    monkeypatch.setattr(repo, "download_asset", lambda week, name, dest: tmp_path / name)
    monkeypatch.setattr(render, "cover_frame", lambda v, png, t=0.9: png)
    assert cli.refresh_covers(repo) == 1 and sent == ["YT1"]
    assert repo.queue()[0]["cover_set"] is True
    assert cli.refresh_covers(repo) == 0


def test_daily_thumbnail_digest_lists_todays_youtube_shorts(repo):
    pub = lambda i, t, yt: {"id": i, "kind": "short", "title": f"T {i}", "published_at": t, "status": "published",
                            "platforms": {"youtube": {"status": "published", "ref": yt}}}
    repo.save_queue([pub("s1", "2026-10-20T11:00:01+00:00", "AAA"), pub("s2", "2026-10-19T08:00:00+00:00", "OLD"),
                     {"id": "l1", "kind": "long", "published_at": "2026-10-20T12:00:00+00:00", "status": "published",
                      "platforms": {"youtube": {"status": "published", "ref": "LONG"}}}])
    n = cli.daily_digest(repo, datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc))
    title, body = repo.issues[0][-3], repo.issues[0][-1]
    assert n == 1 and "2026-10-20" in title
    assert "https://studio.youtube.com/video/AAA/edit" in body and "OLD" not in body and "LONG" not in body


def test_shorts_job_skips_episodes_and_episode_job_skips_shorts(repo):
    repo.write_batch("2026-W44", json.dumps(make_batch()))
    seen = []

    def producer(entry, batch, r, today):
        seen.append(entry["kind"])
        entry["status"] = "produced"
    cli.produce(repo, NOW, 24 * 7, dry_run=False, producer=producer, kinds={"short"})
    assert seen and set(seen) == {"short"}
    seen.clear()
    cli.produce(repo, NOW, 24 * 7, dry_run=False, producer=producer, kinds={"long"})
    assert seen and set(seen) == {"long"}


def test_a_long_render_never_overwrites_what_was_published_meanwhile(repo):
    repo.write_batch("2026-W44", json.dumps(make_batch()))
    cli.intake(repo, NOW.date(), NOW)

    def producer(entry, batch, r, today):
        # while the episode renders, the Shorts job publishes a Short and saves the queue
        q = r.queue()
        q[0]["status"] = "published"
        r.save_queue(q)
        assert next(e for e in q if e["id"] == entry["id"])["producing_since"]   # claimed before rendering
        entry["status"] = "produced"
    cli.produce(repo, NOW, 24 * 7, dry_run=False, producer=producer, kinds={"long"})
    q = repo.queue()
    assert q[0]["status"] == "published"
    longs = [e for e in q if e["kind"] == "long"]
    assert longs and all(e["status"] == "produced" and "producing_since" not in e for e in longs)


def test_an_entry_claimed_by_another_job_is_left_alone(repo):
    repo.write_batch("2026-W44", json.dumps(make_batch()))
    cli.intake(repo, NOW.date(), NOW)
    q = repo.queue()
    first = next(e for e in q if e["kind"] == "short")
    first["producing_since"] = datetime.now(timezone.utc).isoformat()
    repo.save_queue(q)
    seen = []
    cli.produce(repo, NOW, 48, dry_run=False, producer=lambda e, *a: seen.append(e["id"]), kinds={"short"})
    assert first["id"] not in seen and seen
