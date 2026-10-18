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


def _page():
    return env("FB_PAGE_ID"), env("FB_PAGE_TOKEN")


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


def ig_reel(public_url: str, caption: str) -> str:
    ig_id, token = env("IG_USER_ID"), env("FB_PAGE_TOKEN")
    c = _check(requests.post(f"{GRAPH}/{ig_id}/media", data={"media_type": "REELS", "video_url": public_url,
                                                            "caption": caption, "share_to_feed": "true",
                                                            "access_token": token}, timeout=60))
    _ig_wait(c["id"], token)
    return _check(requests.post(f"{GRAPH}/{ig_id}/media_publish", data={"creation_id": c["id"], "access_token": token},
                                timeout=60))["id"]


def fb_comment(object_id: str, text: str) -> str:
    _, token = _page()
    return _check(requests.post(f"{GRAPH}/{object_id}/comments", data={"message": text, "access_token": token}, timeout=60)).get("id", "")


def ig_comment(media_id: str, text: str) -> str:
    token = env("FB_PAGE_TOKEN")
    return _check(requests.post(f"{GRAPH}/{media_id}/comments", data={"message": text, "access_token": token}, timeout=60)).get("id", "")
