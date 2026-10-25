"""YouTube Data API v3: resumable upload, custom thumbnail, first comment (spec §10)."""
import json
import os
from pathlib import Path

import requests

from ..config import env

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"


class YouTubeError(RuntimeError):
    pass


def access_token() -> str:
    r = requests.post(TOKEN_URL, data={"client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
                                       "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=30)
    data = r.json()
    if "access_token" not in data:
        raise YouTubeError(f"token refresh failed (HTTP {r.status_code})")
    return data["access_token"]


def _check(r) -> dict:
    try:
        data = r.json() if r.content else {}
    except ValueError:
        data = {}
    if r.status_code >= 400:
        err = data.get("error", {})
        raise YouTubeError(f"YouTube HTTP {r.status_code}")
    return data


def video_body(title: str, description: str, tags: list[str], is_short: bool) -> dict:
    if is_short and "#shorts" not in title.lower() and len(title) + 8 <= 100:
        title = f"{title} #Shorts"
    return {"snippet": {"title": title[:100], "description": description[:4900], "tags": tags[:15],
                        "categoryId": "24", "defaultLanguage": "en", "defaultAudioLanguage": "en"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": False, "embeddable": True}}


def upload_video(path: Path, title: str, description: str, tags: list[str], is_short: bool, token: str | None = None) -> dict:
    token = token or access_token()
    body = video_body(title, description, tags, is_short)
    size = os.path.getsize(path)
    init = requests.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                         headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                                  "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                         data=json.dumps(body), timeout=60)
    _check(init)
    location = init.headers.get("Location")
    if not location:
        raise YouTubeError("no resumable upload URL returned")
    with open(path, "rb") as f:
        r = requests.put(location, headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                                            "Content-Length": str(size)}, data=f, timeout=3600)
    data = _check(r)
    return {"id": data["id"], "privacy": data.get("status", {}).get("privacyStatus")}


def set_thumbnail(video_id: str, png: Path, token: str | None = None) -> None:
    token = token or access_token()
    with open(png, "rb") as f:
        _check(requests.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set", params={"videoId": video_id},
                             headers={"Authorization": f"Bearer {token}", "Content-Type": "image/png"}, data=f, timeout=120))


def add_comment(video_id: str, text: str, token: str | None = None) -> str:
    token = token or access_token()
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}}
    data = _check(requests.post(f"{API}/commentThreads", params={"part": "snippet"},
                                headers={"Authorization": f"Bearer {token}"}, json=body, timeout=60))
    return data.get("id", "")

