"""Regression tests for the final-review findings (C1-C3, I1-I7)."""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from fbc import cli, schedule
from fbc.models import BatchError, parse_batch
from fbc.store import ContentRepo
from tests.conftest import make_batch

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)


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


# C2 -------------------------------------------------------------------------------------
@pytest.mark.parametrize("mutate", [
    lambda b: b["shorts"].pop(3),                                           # a deleted Short
    lambda b: b["shorts"][0]["questions"][0].__setitem__("answer_index", "1"),
    lambda b: b["shorts"].append(b["shorts"][0]),                           # duplicate id / extra short
])
def test_bad_batch_shapes_raise_batch_error(mutate):
    b = make_batch(); mutate(b)
    with pytest.raises(BatchError):
        parse_batch(json.dumps(b))


def test_top_level_list_is_batch_error():
    with pytest.raises(BatchError):
        parse_batch("[]")


def test_intake_turns_any_error_into_issue(repo):
    b = make_batch(); b["week"] = "garbage"
    repo.write_batch("2026-W44", json.dumps(b))
    cli.intake(repo, NOW.date(), NOW)
    assert repo.queue() == [] and repo.issues


# C1 -------------------------------------------------------------------------------------
def test_retries_do_not_block_new_items(repo, monkeypatch):
    def entry(i, mins_ago, failed):
        plats = {p: {"status": "published" if (not failed or p == "youtube") else "failed", "ref": "r", "error": None,
                     "attempts": 1 if failed and p != "youtube" else 0} for p in ("youtube", "instagram", "facebook")}
        if not failed:
            plats = {p: {"status": "pending", "ref": None, "error": None, "attempts": 0} for p in plats}
        return {"id": i, "kind": "short", "week": "2026-W44", "status": "produced", "asset": f"{i}.mp4",
                "slot_utc": (NOW - timedelta(minutes=mins_ago)).isoformat(), "platforms": plats,
                "title": "T", "description": "D", "first_comment": "C"}
    repo.save_queue([entry("old1", 600, True), entry("old2", 300, True), entry("new", 5, False)])
    seen = []
    monkeypatch.setattr(repo, "download_asset", lambda w, n, d: d / n)
    import fbc.publish as pub
    monkeypatch.setattr(pub, "publish_item", lambda e, *a: seen.append(e["id"]) or e)
    cli.publish(repo, NOW, None, dry_run=False)
    assert "new" in seen


# I1 -------------------------------------------------------------------------------------
def test_skipped_and_old_entries_do_not_escalate():
    q = [{"id": "s", "status": "skipped", "slot_utc": (NOW - timedelta(hours=5)).isoformat()},
         {"id": "old", "status": "failed", "slot_utc": (NOW - timedelta(days=3)).isoformat()},
         {"id": "late", "status": "planned", "slot_utc": (NOW - timedelta(hours=3)).isoformat()}]
    assert [e["id"] for e in schedule.late_items(q, NOW)] == ["late"]


# I3 -------------------------------------------------------------------------------------
def test_production_failure_retries_before_failing(repo):
    repo.write_batch("2026-W44", json.dumps(make_batch()))

    def boom(entry, *a):
        raise RuntimeError("pexels 503")
    cli.produce(repo, NOW, 48, dry_run=False, producer=boom)
    first = repo.queue()[0]
    assert first["status"] == "planned" and first["produce_attempts"] == 1 and not repo.issues
    cli.produce(repo, NOW, 48, dry_run=False, producer=boom)
    cli.produce(repo, NOW, 48, dry_run=False, producer=boom)
    assert repo.queue()[0]["status"] == "failed" and repo.issues


# C3 -------------------------------------------------------------------------------------
def test_gemini_backs_off_then_succeeds(monkeypatch):
    from fbc import gemini
    monkeypatch.setenv("GEMINI_API_KEY", "k"); monkeypatch.setenv("GEMINI_TEXT_MODEL", "m")
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.headers, self.text = code, body, {"Retry-After": "2"}, ""

        def json(self):
            return self._b
    def post(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            return R(429, {})
        return R(200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    monkeypatch.setattr(gemini.requests, "post", post)
    assert gemini.generate_json("x", {}) == {}
    assert len(calls) == 3


# I5 -------------------------------------------------------------------------------------
def test_long_video_goes_to_fb_page_video_and_skips_ig(monkeypatch, tmp_path):
    from datetime import date
    import fbc.publish as pub
    from fbc.publish import meta, youtube
    monkeypatch.setattr(youtube, "access_token", lambda: "t")
    monkeypatch.setattr(youtube, "upload_video", lambda *a, **k: {"id": "y", "privacy": "public"})
    monkeypatch.setattr(youtube, "set_thumbnail", lambda *a, **k: None)
    monkeypatch.setattr(youtube, "add_comment", lambda *a, **k: "c")
    monkeypatch.setattr(meta, "fb_reel", lambda *a: pytest.fail("long video must not go to Reels"))
    monkeypatch.setattr(meta, "fb_video", lambda *a: "fbv")
    monkeypatch.setattr(meta, "fb_comment", lambda *a: "c")
    e = {"id": "l", "status": "produced", "platforms": {p: {"status": "pending", "ref": None, "error": None, "attempts": 0}
                                                         for p in ("youtube", "instagram", "facebook")}}
    pub.publish_item(e, {"title": "T", "description": "D", "first_comment": "C", "is_short": False}, tmp_path / "v.mp4",
                     None, date(2026, 10, 31))
    assert e["platforms"]["instagram"]["status"] == "skipped" and e["status"] == "published"


# I6 / I7 --------------------------------------------------------------------------------
def test_zoom_prefers_subject_photo_over_generic(monkeypatch):
    from fbc import pexels
    monkeypatch.setenv("PEXELS_API_KEY", "p")

    class R:
        status_code = 200
        def __init__(self, d): self.d = d
        def json(self): return self.d
        def raise_for_status(self): pass
    def get(url, headers=None, params=None, timeout=None):
        assert params.get("size") != "large"
        if "videos" in url:
            hit = params["query"] in pexels.GENERIC_QUERIES
            return R({"videos": [{"id": 1, "duration": 10, "video_files": [{"width": 1080, "height": 1920, "link": "v",
                                                                             "file_type": "video/mp4"}]}] if hit else []})
        return R({"photos": [{"id": 2, "width": 3000, "height": 4000, "src": {"original": "p"}}]})
    monkeypatch.setattr(pexels.requests, "get", get)
    assert pexels.find_clip("pangolin", "portrait", set(), subject_required=True).kind == "photo"
    assert pexels.find_clip("pangolin", "portrait", set()).kind == "video"


def test_pexels_429_means_resume_later(monkeypatch):
    from fbc import pexels
    from fbc.errors import ResumeLater
    monkeypatch.setenv("PEXELS_API_KEY", "p")

    class R:
        status_code = 429
        def json(self): return {}
        def raise_for_status(self): raise AssertionError("should not reach")
    monkeypatch.setattr(pexels.requests, "get", lambda *a, **k: R())
    with pytest.raises(ResumeLater):
        pexels.find_clip("x", "portrait", set())
