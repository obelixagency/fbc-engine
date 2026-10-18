"""Publishes one queue entry to every platform independently (spec §10, Review Focus 4):
a failure on one platform never blocks the others, and each retry only touches what failed."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from ..log import safe
from . import media_host, meta, youtube

HASHTAGS = ["#quiz", "#trivia", "#familyfun"]
SOCIAL_TAGS = "#quiz #trivia #familyfun #brainteasers #quiznight #familygamenight"
YT_TAGS = ["family quiz", "trivia", "quiz", "brain teasers", "riddles", "family game night", "general knowledge"]


def captions(meta_: dict) -> dict:
    desc = meta_["description"].strip()
    if meta_["is_short"]:
        yt = f"{desc}\n\nNew Family Quiz Night every Saturday on our channel!\n\n{' '.join(HASHTAGS)}"
    else:
        chapters = "\n".join(meta_.get("chapters", []))
        yt = f"{desc}\n\n{chapters}\n\n{' '.join(HASHTAGS)}".strip()
    social = f"{meta_['title']}\n\n{desc}\n\n{SOCIAL_TAGS}"
    return {"youtube": yt, "social": social}


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:300]}"


def publish_item(entry: dict, meta_: dict, video: Path, media_repo: Path | None, today: date) -> dict:
    """meta_: title, description, first_comment, is_short, chapters, thumbnails (list of PNG paths, long only)."""
    caps = captions(meta_)
    plats = entry["platforms"]

    def run(name, fn):
        p = plats[name]
        if p["status"] == "published":
            return
        try:
            p["ref"], p["status"], p["error"] = fn(), "published", None
            safe("published", id=entry["id"], platform=name, status="ok")
        except Exception as e:  # each platform is independent
            p["status"], p["error"] = "failed", _err(e)
            p["attempts"] = p.get("attempts", 0) + 1
            safe("publish_failed", id=entry["id"], platform=name, attempts=p["attempts"], error_type=type(e).__name__)

    def yt():
        token = youtube.access_token()
        res = youtube.upload_video(video, meta_["title"], caps["youtube"], YT_TAGS, meta_["is_short"], token=token)
        for png in meta_.get("thumbnails", [])[:1]:
            try:
                youtube.set_thumbnail(res["id"], png, token=token)
            except Exception as e:  # thumbnail needs a phone-verified channel; never blocks the upload
                plats["youtube"]["note"] = _err(e)
        if meta_.get("first_comment"):
            try:
                youtube.add_comment(res["id"], meta_["first_comment"], token=token)
            except Exception as e:
                plats["youtube"]["note"] = _err(e)
        if res.get("privacy") == "private":
            plats["youtube"]["note"] = "private_pending_audit: publish it by hand in YouTube Studio until the API audit passes"
        return res["id"]

    def fb():
        # Reels accept short vertical video only; the long episode goes out as a regular Page video.
        vid = meta.fb_reel(video, caps["social"]) if meta_["is_short"] else meta.fb_video(video, meta_["title"], caps["social"])
        if meta_.get("first_comment"):
            try:
                meta.fb_comment(vid, meta_["first_comment"])
            except Exception:
                pass
        return vid

    def ig():
        if media_repo is None:
            raise RuntimeError("media repo not available for the public video URL")
        url = media_host.push_public(media_repo, video, today)
        mid = meta.ig_reel(url, caps["social"])
        if meta_.get("first_comment"):
            try:
                meta.ig_comment(mid, meta_["first_comment"])
            except Exception:
                pass
        return mid

    run("youtube", yt)
    run("facebook", fb)
    if meta_["is_short"]:
        run("instagram", ig)
    else:  # Reels are short-form only; a 10-minute episode is not posted to Instagram
        plats["instagram"]["status"] = "skipped"
    if all(p["status"] in ("published", "skipped") for p in plats.values()):
        entry["status"] = "published"
    return entry
