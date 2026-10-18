"""Automatic QA before anything is published (spec §11). Any problem blocks the video."""
import json
import re
import subprocess
from pathlib import Path

from .config import LIMITS

SIZE = {"short": (1080, 1920), "long": (1920, 1080)}


def _probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


def _silences(path: Path, min_len: float = 1.2) -> list[tuple[float, float]]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"silencedetect=noise=-45dB:d={min_len}", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    if len(ends) < len(starts):
        ends.append(float("inf"))
    return list(zip(starts, ends))


def check_video(path: Path, kind: str, meta: dict | None = None) -> list[str]:
    meta = meta or {}
    problems = []
    info = _probe(path)
    if not info.get("streams"):
        return [f"{path.name}: not a readable video"]
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    dur = float(info.get("format", {}).get("duration", 0))
    lo = meta.get("min", LIMITS["short_min_s"] if kind == "short" else LIMITS["long_min_s"])
    hi = LIMITS["short_max_s"] if kind == "short" else LIMITS["long_max_s"]
    if not lo - 0.2 <= dur <= hi + 0.2:
        problems.append(f"duration {dur:.1f}s outside {lo:.0f}-{hi:.0f}s")
    if v is None or (v.get("width"), v.get("height")) != SIZE[kind]:
        problems.append(f"resolution {v and v.get('width')}x{v and v.get('height')} (need {SIZE[kind][0]}x{SIZE[kind][1]})")
    if v is not None and v.get("r_frame_rate") not in ("30/1", "30000/1000"):
        problems.append(f"frame rate {v.get('r_frame_rate')} (need 30)")
    if a is None:
        problems.append("no audio stream")
    else:
        for s0, s1 in _silences(path):
            for w0, w1 in meta.get("voice", []):
                if s0 < w1 - 0.05 and s1 > w0 + 0.05 and min(s1, w1) - max(s0, w0) >= 1.0:
                    problems.append(f"silence {s0:.1f}-{min(s1, dur):.1f}s inside a voice line ({w0:.1f}-{w1:.1f}s)")
                    break
    return problems
