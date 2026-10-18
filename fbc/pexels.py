"""Pexels visuals: video first, then other orientation (cropped later), then photo (Ken Burns)."""
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import env
from .errors import ResumeLater

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
    if r.status_code == 429:
        raise ResumeLater("Pexels hourly limit reached")
    r.raise_for_status()
    return r.json()


def _best_file(video: dict) -> dict | None:
    ok = [f for f in video.get("video_files", [])
          if f.get("file_type") == "video/mp4" and min(f.get("width") or 0, f.get("height") or 0) >= MIN_SHORT_SIDE]
    return min(ok, key=lambda f: f["width"] * f["height"]) if ok else None


def _videos(query: str, orientation: str, exclude: set[int]) -> Clip | None:
    data = _get("/videos/search", {"query": query, "orientation": orientation, "size": "medium",
                                   "per_page": 15, "min_duration": MIN_DURATION})
    for v in data.get("videos", []):
        if v["id"] in exclude or (v.get("duration") or 0) < MIN_DURATION:
            continue
        f = _best_file(v)
        if f:
            return Clip("video", v["id"], f["link"], f["width"], f["height"], float(v.get("duration") or 0))
    return None


# Quiz-friendly moving backgrounds used when the subject itself has no usable video,
# so every Short keeps a moving video background (photos are a last resort only).
GENERIC_QUERIES = ["colorful bokeh lights", "abstract colorful particles", "neon light background",
                   "colorful smoke slow motion", "confetti falling", "colorful paint swirl"]


def _photo(query: str, orientation: str, exclude_ids: set[int]) -> Clip | None:
    data = _get("/v1/search", {"query": query, "orientation": orientation, "per_page": 15})
    for p in data.get("photos", []):
        if p["id"] not in exclude_ids:
            return Clip("photo", p["id"], p["src"].get("large2x") or p["src"]["original"], p["width"], p["height"])
    return None


def find_clip(query: str, orientation: str, exclude_ids: set[int], subject_required: bool = False) -> Clip:
    """Subject video (either orientation) first. Then, when the picture IS the question (zoom reveal),
    a subject photo; otherwise a generic quiz-style moving background, and a photo only as last resort."""
    other = "landscape" if orientation == "portrait" else "portrait"
    for o in (orientation, other):
        clip = _videos(query, o, exclude_ids)
        if clip:
            return clip
    if subject_required:
        clip = _photo(query, orientation, exclude_ids)
        if clip:
            return clip
    start = sum(map(ord, query)) % len(GENERIC_QUERIES)  # stable but varied per question
    for k in range(len(GENERIC_QUERIES)):
        clip = _videos(GENERIC_QUERIES[(start + k) % len(GENERIC_QUERIES)], orientation, exclude_ids)
        if clip:
            return clip
    clip = _photo(query, orientation, exclude_ids)
    if clip:
        return clip
    raise NoVisual(query)


def download(clip: Clip, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(clip.url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest
