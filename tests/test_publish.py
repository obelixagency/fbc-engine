import json
from datetime import date
from pathlib import Path

import pytest

from fbc import publish
from fbc.publish import meta, youtube


class R:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._d, self.headers = status, data or {}, headers or {}
        self.content = json.dumps(self._d).encode()

    def json(self):
        return self._d


@pytest.fixture(autouse=True)
def secrets(monkeypatch):
    for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN", "FB_PAGE_ID", "FB_PAGE_TOKEN", "IG_USER_ID"):
        monkeypatch.setenv(k, "x")


def test_youtube_body_fixed_values():
    b = youtube.video_body("Only 3% Get This", "d", ["quiz"], is_short=True)
    assert b["status"]["selfDeclaredMadeForKids"] is False
    assert b["status"]["containsSyntheticMedia"] is False
    assert b["snippet"]["categoryId"] == "24" and b["snippet"]["defaultLanguage"] == "en"
    assert b["snippet"]["title"].endswith("#Shorts")
    assert not youtube.video_body("Quiz Night", "d", [], is_short=False)["snippet"]["title"].endswith("#Shorts")


def test_youtube_upload_sends_body(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"0" * 100)
    sent = {}

    def post(url, params=None, headers=None, data=None, json=None, timeout=None):
        sent["body"] = data
        return R(200, {}, {"Location": "https://upload/session"})
    monkeypatch.setattr(youtube.requests, "post", post)
    monkeypatch.setattr(youtube.requests, "put", lambda *a, **k: R(200, {"id": "yt1", "status": {"privacyStatus": "public"}}))
    out = youtube.upload_video(video, "T", "D", [], True, token="t")
    assert out == {"id": "yt1", "privacy": "public"}
    body = __import__("json").loads(sent["body"])
    assert body["status"]["selfDeclaredMadeForKids"] is False


def test_fb_reel_three_phase(monkeypatch, tmp_path):
    video = tmp_path / "v.mp4"; video.write_bytes(b"0" * 10)
    calls = []

    def post(url, data=None, headers=None, timeout=None):
        calls.append((url, dict(data) if isinstance(data, dict) else "bytes"))
        if isinstance(data, dict) and data.get("upload_phase") == "start":
            return R(200, {"video_id": "fb1", "upload_url": "https://rupload/fb1"})
        return R(200, {"success": True})
    monkeypatch.setattr(meta.requests, "post", post)
    assert meta.fb_reel(video, "cap") == "fb1"
    assert calls[0][0].endswith("/video_reels") and calls[1][0] == "https://rupload/fb1"
    assert calls[2][1]["upload_phase"] == "finish" and calls[2][1]["video_state"] == "PUBLISHED"


def entry():
    return {"id": "s1", "status": "produced", "platforms": {p: {"status": "pending", "ref": None, "error": None, "attempts": 0}
                                                            for p in ("youtube", "instagram", "facebook")}}


META = {"title": "T", "description": "D", "first_comment": "Score?", "is_short": True}


def test_one_platform_failure_does_not_block_others(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube, "access_token", lambda: "t")
    monkeypatch.setattr(youtube, "upload_video", lambda *a, **k: {"id": "yt1", "privacy": "public"})
    monkeypatch.setattr(youtube, "add_comment", lambda *a, **k: "c")
    monkeypatch.setattr(meta, "fb_reel", lambda *a: "fb1")
    monkeypatch.setattr(meta, "fb_comment", lambda *a: "c")
    monkeypatch.setattr(publish.media_host, "push_public", lambda *a: "https://pub/v.mp4")

    def ig_fail(*a):
        raise meta.MetaError("token expired")
    monkeypatch.setattr(meta, "ig_reel", ig_fail)
    e = publish.publish_item(entry(), META, tmp_path / "v.mp4", tmp_path, date(2026, 10, 27))
    p = e["platforms"]
    assert p["youtube"]["status"] == "published" and p["facebook"]["status"] == "published"
    assert p["instagram"]["status"] == "failed" and p["instagram"]["attempts"] == 1
    assert e["status"] == "produced"

    calls = []
    monkeypatch.setattr(youtube, "upload_video", lambda *a, **k: calls.append("yt") or {"id": "x"})
    monkeypatch.setattr(meta, "fb_reel", lambda *a: calls.append("fb") or "x")
    monkeypatch.setattr(meta, "ig_reel", lambda *a: calls.append("ig") or "ig1")
    monkeypatch.setattr(meta, "ig_comment", lambda *a: "c")
    e = publish.publish_item(e, META, tmp_path / "v.mp4", tmp_path, date(2026, 10, 27))
    assert calls == ["ig"] and e["status"] == "published"


def test_private_upload_is_noted(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube, "access_token", lambda: "t")
    monkeypatch.setattr(youtube, "upload_video", lambda *a, **k: {"id": "yt1", "privacy": "private"})
    monkeypatch.setattr(youtube, "add_comment", lambda *a, **k: "c")
    monkeypatch.setattr(meta, "fb_reel", lambda *a: "fb1")
    monkeypatch.setattr(meta, "fb_comment", lambda *a: "c")
    monkeypatch.setattr(publish.media_host, "push_public", lambda *a: "u")
    monkeypatch.setattr(meta, "ig_reel", lambda *a: "ig1")
    monkeypatch.setattr(meta, "ig_comment", lambda *a: "c")
    e = publish.publish_item(entry(), META, tmp_path / "v.mp4", tmp_path, date(2026, 10, 27))
    assert "private_pending_audit" in e["platforms"]["youtube"]["note"]
