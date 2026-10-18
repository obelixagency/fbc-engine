"""Pexels visuals: video first, then other orientation (cropped later), then photo (Ken Burns)."""
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import env

API = "https://api.pexels.com"
MIN_SHORT_SIDE = 1080
MIN_DURATION = 6


class NoVisual(RuntimeError):
    pass


@dataclass
class Clip:
    kind: str           # "video" | "photo"
    pexels_id: int
    url: str
    width: int
    height: int
    duration: float | None = None


def _get(path: str, params: dict) -> dict:
    r = requests.get(f"{API}{path}", headers={"Authorization": env("PEXELS_API_KEY")}, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _best_file(video: dict) -> dict | None:
    ok = [f for f in video.get("video_files", [])
          if f.get("file_type") == "video/mp4" and min(f.get("width") or 0, f.get("height") or 0) >= MIN_SHORT_SIDE]
    return min(ok, key=lambda f: f["width"] * f["height"]) if ok else None


def _videos(query: str, orientation: str, exclude: set[int]) -> Clip | None:
    data = _get("/videos/search", {"query": query, "orientation": orientation, "size": "large",
                                   "per_page": 15, "min_duration": MIN_DURATION})
    for v in data.get("videos", []):
        if v["id"] in exclude or (v.get("duration") or 0) < MIN_DURATION:
            continue
        f = _best_file(v)
        if f:
            return Clip("video", v["id"], f["link"], f["width"], f["height"], float(v.get("duration") or 0))
    return None


def find_clip(query: str, orientation: str, exclude_ids: set[int]) -> Clip:
    other = "landscape" if orientation == "portrait" else "portrait"
    for o in (orientation, other):
        clip = _videos(query, o, exclude_ids)
        if clip:
            return clip
    data = _get("/v1/search", {"query": query, "orientation": orientation, "per_page": 15})
    for p in data.get("photos", []):
        if p["id"] not in exclude_ids:
            return Clip("photo", p["id"], p["src"].get("large2x") or p["src"]["original"], p["width"], p["height"])
    raise NoVisual(query)


def download(clip: Clip, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(clip.url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest
