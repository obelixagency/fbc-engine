"""Facebook Page Reels and Instagram Reels via the Graph API (adapted from Unshaken's publish.py).
Facebook uses the Reels upload flow so videos land in the Reels tab; Instagram pulls a public URL."""
import os
import time
from pathlib import Path

import requests

from ..config import env

GRAPH = "https://graph.facebook.com/v26.0"


class MetaError(RuntimeError):
    pass


def _check(resp) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise MetaError(f"non-JSON response (HTTP {resp.status_code})")
    if resp.status_code >= 400 or "error" in data:
        err = data.get("error", {})
        raise MetaError(f"Meta HTTP {resp.status_code}: {err.get('message') if isinstance(err, dict) else err}")
    return data


_PAGE_TOKEN: dict = {}


def page_token() -> str:
    """FB_PAGE_TOKEN may be the Page token itself or a permanent Business system-user token: in the second case
    the Page token is fetched with it (a Page token from a non-expiring user token never expires either)."""
    raw, page_id = env("FB_PAGE_TOKEN"), env("FB_PAGE_ID")
    if raw not in _PAGE_TOKEN:
        me = _check(requests.get(f"{GRAPH}/me", params={"fields": "id", "access_token": raw}, timeout=30))
        if str(me.get("id")) == str(page_id):
            _PAGE_TOKEN[raw] = raw
        else:
            page = _check(requests.get(f"{GRAPH}/{page_id}", params={"fields": "access_token", "access_token": raw},
                                       timeout=30))
            if not page.get("access_token"):
                raise MetaError("the FB_PAGE_TOKEN user has no access to the Page (assign the Page to it)")
            _PAGE_TOKEN[raw] = page["access_token"]
    return _PAGE_TOKEN[raw]


def _page():
    return env("FB_PAGE_ID"), page_token()


def fb_reel(path: Path, caption: str) -> str:
    page_id, token = _page()
    start = _check(requests.post(f"{GRAPH}/{page_id}/video_reels", data={"upload_phase": "start", "access_token": token}, timeout=60))
    video_id, upload_url = start["video_id"], start["upload_url"]
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        _check(requests.post(upload_url, headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
                             data=f, timeout=1800))
    _check(requests.post(f"{GRAPH}/{page_id}/video_reels", data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED", "description": caption,
        "access_token": token}, timeout=60))
    return video_id


def fb_video(path: Path, title: str, description: str) -> str:
    """Regular (non-Reel) Page video, used for the long landscape episode."""
    page_id, token = _page()
    with open(path, "rb") as f:
        data = _check(requests.post(f"https://graph-video.facebook.com/v26.0/{page_id}/videos",
                                    data={"title": title, "description": description, "access_token": token},
                                    files={"source": f}, timeout=3600))
    return data["id"]


def _ig_wait(creation_id: str, token: str, tries: int = 60, wait_s: float = 5) -> None:
    for _ in range(tries):
        status = _check(requests.get(f"{GRAPH}/{creation_id}", params={"fields": "status_code", "access_token": token}, timeout=30))
        code = status.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise MetaError("Instagram could not process the video")
        time.sleep(wait_s)
    raise MetaError("Instagram container never finished processing")


def ig_reel(public_url: str, caption: str, thumb_offset_ms: int | None = None, cover_url: str | None = None) -> str:
    ig_id, token = env("IG_USER_ID"), page_token()
    data = {"media_type": "REELS", "video_url": public_url, "caption": caption, "share_to_feed": "true",
            "access_token": token}
    if cover_url:                     # designed cover image (the hook), never the revealed answer
        data["cover_url"] = cover_url
    elif thumb_offset_ms is not None:
        data["thumb_offset"] = str(int(thumb_offset_ms))
    c = _check(requests.post(f"{GRAPH}/{ig_id}/media", data=data, timeout=60))
    _ig_wait(c["id"], token)
    return _check(requests.post(f"{GRAPH}/{ig_id}/media_publish", data={"creation_id": c["id"], "access_token": token},
                                timeout=60))["id"]


def fb_thumbnail(video_id: str, png: Path) -> None:
    """Preferred thumbnail for a Page video/Reel."""
    token = page_token()
    with open(png, "rb") as f:
        _check(requests.post(f"{GRAPH}/{video_id}/thumbnails", data={"is_preferred": "true", "access_token": token},
                             files={"source": (png.name, f, "image/png")}, timeout=120))


def fb_comment(object_id: str, text: str) -> str:
    _, token = _page()
    return _check(requests.post(f"{GRAPH}/{object_id}/comments", data={"message": text, "access_token": token}, timeout=60)).get("id", "")


def ig_comment(media_id: str, text: str) -> str:
    token = page_token()
    return _check(requests.post(f"{GRAPH}/{media_id}/comments", data={"message": text, "access_token": token}, timeout=60)).get("id", "")
