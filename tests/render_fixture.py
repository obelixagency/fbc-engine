"""Builds a renderable project from the valid fixture batch with synthetic audio and clips
(ffmpeg test sources), so rendering can be checked without any API key."""
import subprocess
from pathlib import Path

from fbc import audio, render, timeline
from fbc.models import batch_from_obj
from tests.conftest import make_batch


def synth_wav(path: Path, seconds: float, freq: int = 300):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={seconds}", "-ar", "24000", "-ac", "1", str(path)], check=True)


def synth_clip(path: Path, w: int, h: int, seconds: float = 8, color="0x2B5F8A"):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size={w}x{h}:rate=30:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path)], check=True)


def build(work: Path, which: str = "short", index: int = 0, line_s: float = 1.3):
    batch = batch_from_obj(make_batch())
    item = batch.shorts[index] if which == "short" else batch.long
    req = audio.build_audio_request(item)
    vdir = work / "voice"; vdir.mkdir(parents=True, exist_ok=True)
    for line in req["lines"]:
        p = vdir / f"{line['id']}.wav"
        if not p.exists():
            synth_wav(p, line_s if not line["id"].endswith("reveal") else line_s + 0.8)
    durations = {l["id"]: audio.wav_duration(vdir / f"{l['id']}.wav") for l in req["lines"]}
    audio.make_sfx(work / "sfx")
    w, h = (1080, 1920) if which == "short" else (1920, 1080)
    raw = work / f"raw_{w}x{h}.mp4"
    if not raw.exists():
        synth_clip(raw, w, h)
    qs = item.questions if which == "short" else item.all_questions()
    clips, assets = {}, {}
    for q in qs:
        name = f"{q.id}.mp4"
        clips[q.id] = {"name": name, "kind": "video", "portrait": which == "short"}
        assets[name] = raw
    tl = (timeline.build_short_timeline(item, durations, clips) if which == "short"
          else timeline.build_long_timeline(item, durations, clips))
    for name in {a for s in tl.segments for a, _ in s.audio}:
        assets[name] = vdir / name
    for name in ("tick.wav", "ding.wav", "whoosh.wav"):
        assets[name] = work / "sfx" / name
    proj = render.write_project(tl, {k: v for k, v in durations.items()}, assets, work / "project", which)
    return proj, tl
