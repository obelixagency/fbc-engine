"""Timeline -> HyperFrames project -> MP4 (and PNG thumbnails for the long video).

Python decides every element and every animation time; the template only lays them out, and
its inline script replays the action list into one paused GSAP timeline (seekable, deterministic)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import BRAND, HYPERFRAMES
from .timeline import Timeline

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))


class RenderError(RuntimeError):
    pass


QUESTION_KINDS = {"mcq", "tf", "riddle", "zoom", "wyr"}
PARTS = {"hook": ("-big",), "outro": ("-big", "-sub"), "intro": ("-pill", "-big", "-sub"),
         "round_title": ("-pill", "-big"), "score": ("-big", "-sub"), "tiers": ("-big", "-tiers"),
         "end": ("-big", "-sub")}
DY = {"-pill": 30, "-big": 60, "-q": 60, "-sub": 40, "-tiers": 40}


def _layout(tl: Timeline) -> dict:
    portrait = tl.height > tl.width
    if portrait:  # 1080x1920, u = 20px; safe zone: top 250px, bottom 420px
        return dict(U=20, BRAND_TOP=1.8, STAGE_L=4, STAGE_R=4, STAGE_T=13.5, STAGE_B=22,
                    Q_SIZE=4.6, OPT_SIZE=3.2, BIG_SIZE=6.2, OVERLAY=0.5, GAP=1.8)
    # 1920x1080, u = 20px; questions: picture panel on the left, text on the right
    return dict(U=20, BRAND_TOP=2.2, STAGE_L=52, STAGE_R=4, STAGE_T=8, STAGE_B=5,
                Q_SIZE=3.4, OPT_SIZE=2.6, BIG_SIZE=4.6, OVERLAY=0.62, GAP=1.2)


def _reveal_text(seg) -> str:
    if seg.kind == "tf":
        return f"{seg.data['answer']}!"
    return f"It's {seg.data['answer']}!"


def _backgrounds(tl: Timeline) -> list[dict]:
    """One background element per run of consecutive segments that share a clip and a mode."""
    portrait = tl.height > tl.width
    out: list[dict] = []
    for seg in tl.segments:
        if not seg.clip:
            continue
        mode = "panel" if (not portrait and seg.kind in QUESTION_KINDS) else "full"
        last = out[-1] if out else None
        if last and last["name"] == seg.clip["name"] and last["mode"] == mode and abs(last["end"] - seg.start) < 1e-6:
            last["end"] = seg.end
            last["segs"].append(seg)
            continue
        out.append({"name": seg.clip["name"], "kind": seg.clip["kind"], "mode": mode,
                    "start": seg.start, "end": seg.end, "segs": [seg]})
    for bg in out:
        bg["duration"] = round(bg["end"] - bg["start"], 3)
        bg["start"] = round(bg["start"], 3)
    return out


def build_actions(tl: Timeline, backgrounds: list[dict]) -> list[dict]:
    A: list[dict] = []

    def add(m, sel, t, to, frm=None):
        a = {"m": m, "sel": sel, "t": round(t, 3), "to": to}
        if frm is not None:
            a["from"] = frm
        A.append(a)

    add("fromTo", "#progress", 0, {"scaleX": 1, "duration": tl.duration, "ease": "none"}, {"scaleX": 0})
    for i, bg in enumerate(backgrounds):
        sel, seg0 = f"#bgw{i}", bg["segs"][0]
        if seg0.kind == "zoom" and seg0.data.get("reveal_at"):
            add("fromTo", sel, bg["start"], {"scale": 1, "duration": seg0.data["reveal_at"] - bg["start"],
                                              "ease": "power1.inOut"}, {"scale": 4})
        else:
            grow = 1.12 if bg["kind"] == "photo" else 1.06
            add("fromTo", sel, bg["start"], {"scale": grow, "duration": bg["duration"], "ease": "none"}, {"scale": 1})
    for i, seg in enumerate(tl.segments):
        p, t0 = f"#sc{i}", seg.start
        parts = list(PARTS.get(seg.kind, ("-q",)))
        if seg.kind in QUESTION_KINDS and (seg.data.get("label") or seg.data.get("total", 1) > 1):
            parts.insert(0, "-pill")
        for part in parts:
            dy = DY[part]
            add("fromTo", p + part, t0 + 0.05, {"y": 0, "opacity": 1, "duration": 0.4, "ease": "back.out(1.6)"},
                {"y": dy, "opacity": 0})
        if seg.kind == "outro" and tl.loop_safe:
            t_loop = seg.end - 0.45
            add("to", f"{p}-big", t_loop, {"opacity": 0, "duration": 0.3})
            add("to", f"{p}-sub", t_loop, {"opacity": 0, "duration": 0.3})
            add("to", f"{p}-loop", t_loop, {"opacity": 1, "duration": 0.3})
        if seg.kind not in QUESTION_KINDS:
            continue
        n_opts = len(seg.data.get("options") or [])
        for k in range(n_opts):
            add("fromTo", f"{p}-o{k}", t0 + 0.25 + 0.08 * k, {"x": 0, "opacity": 1, "duration": 0.35, "ease": "power3.out"},
                {"x": -80, "opacity": 0})
        if seg.kind == "wyr":
            add("fromTo", f"{p}-vote", seg.data["countdown_start"], {"scale": 1, "opacity": 1, "duration": 0.4,
                                                                       "ease": "back.out(2)"}, {"scale": 0.6, "opacity": 0})
            continue
        cd0, cd = seg.data["countdown_start"], seg.data["countdown"]
        nums = _countdown_numbers(cd)
        add("fromTo", f"{p}-cd", cd0 - 0.15, {"scale": 1, "opacity": 1, "duration": 0.2, "ease": "back.out(2)"},
            {"scale": 0, "opacity": 0})
        for j, _ in enumerate(nums):
            at = cd0 + j * (cd / len(nums))
            add("fromTo", f"{p}-cd{j}", at, {"scale": 1, "opacity": 1, "duration": 0.18, "ease": "back.out(3)"},
                {"scale": 1.6, "opacity": 0})
            add("set", f"{p}-cd{j}", at + cd / len(nums) - 0.01, {"opacity": 0})
        rv = seg.data["reveal_at"]
        add("to", f"{p}-cd", rv, {"scale": 0, "opacity": 0, "duration": 0.2})
        add("set", "#flash", rv, {"opacity": 0.35})   # set+to, not fromTo: a fromTo would pre-apply 0.35 at t=0
        add("to", "#flash", rv + 0.01, {"opacity": 0, "duration": 0.45, "ease": "power2.out"})
        ai = seg.data.get("answer_index", -1)
        for k in range(n_opts):
            if k == ai:
                add("to", f"{p}-o{k}", rv, {"backgroundColor": BRAND["green"], "color": "#0B3D2A", "scale": 1.05,
                                            "duration": 0.25, "ease": "back.out(2)"})
                if seg.kind != "tf":
                    add("to", f"{p}-o{k} b", rv, {"color": "#0B3D2A", "duration": 0.25})
            else:
                add("to", f"{p}-o{k}", rv, {"opacity": 0.45, "duration": 0.25})
        add("fromTo", f"{p}-rv", rv + 0.1, {"y": 0, "opacity": 1, "duration": 0.35, "ease": "back.out(1.8)"},
            {"y": 40, "opacity": 0})
    return A


def _countdown_numbers(cd: float) -> list[int]:
    n = max(1, int(round(cd)))
    return list(range(n, 0, -1))


def _audios(tl: Timeline, durations: dict, sfx_len: dict) -> list[dict]:
    out = []
    for seg in tl.segments:
        for name, at in seg.audio:
            out.append({"name": name, "start": round(at, 3), "duration": round(durations[name[:-4]], 3),
                        "track": 2, "volume": 1})
        for name, at in seg.sfx:
            out.append({"name": name, "start": round(at, 3), "duration": sfx_len[name], "track": 3, "volume": 0.7})
    return out


def write_project(tl: Timeline, durations: dict, assets: dict[str, Path], outdir: Path, kind: str) -> Path:
    """assets: file name -> source path for every wav, sfx and prepared clip the timeline names."""
    outdir.mkdir(parents=True, exist_ok=True)
    adir = outdir / "assets"
    (adir / "fonts").mkdir(parents=True, exist_ok=True)
    for f in (TEMPLATES / "fonts").glob("*.woff2"):
        shutil.copy2(f, adir / "fonts" / f.name)
    shutil.copy2(TEMPLATES / "vendor" / "gsap.min.js", adir / "gsap.min.js")
    for name, src in assets.items():
        dest = adir / name
        if not dest.exists():
            shutil.copy2(src, dest)
    from .audio import wav_duration
    sfx_len = {n: round(wav_duration(adir / n), 3) for n in ("tick.wav", "ding.wav", "whoosh.wav") if (adir / n).exists()}
    backgrounds = _backgrounds(tl)
    scenes = []
    for i, seg in enumerate(tl.segments):
        scenes.append({"idx": i, "kind": seg.kind, "start": round(seg.start, 3), "duration": round(seg.end - seg.start, 3),
                       "data": seg.data, "reveal_text": _reveal_text(seg) if seg.kind in QUESTION_KINDS else "",
                       "countdown_numbers": _countdown_numbers(seg.data.get("countdown") or 0) if seg.data.get("countdown") else [],
                       "outro_text": seg.data.get("outro_text", "")})
    actions = build_actions(tl, backgrounds)
    html = _env.get_template("video.html.j2").render(
        W=tl.width, H=tl.height, DURATION=round(tl.duration, 3), backgrounds=backgrounds, scenes=scenes,
        audios=_audios(tl, durations, sfx_len), actions_json=json.dumps(actions), **_layout(tl))
    (outdir / "index.html").write_text(html)
    voice = [[a["start"], round(a["start"] + a["duration"], 3)] for a in _audios(tl, durations, sfx_len) if a["track"] == 2]
    (outdir / "qa.json").write_text(json.dumps({"kind": kind, "duration": tl.duration, "min": tl.min_duration,
                                                 "voice": voice, "countdowns": tl.countdown_windows(),
                                                 "chapters": tl.chapters}))
    return outdir


def prepare_clip(src: Path, dest: Path, width: int, height: int, seconds: float, kind: str) -> Path:
    """Normalize a Pexels download: crop to the frame, 30 fps, no audio, looped to `seconds`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if kind == "photo":
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf", vf, "-q:v", "3", str(dest)]
    else:
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src), "-t", f"{seconds:.2f}",
               "-vf", vf + ",fps=30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RenderError(f"ffmpeg failed preparing {src.name}: {r.stderr[-400:]}")
    return dest


def _npx(*args: str, cwd: Path | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    return subprocess.run(["npx", "-y", HYPERFRAMES, *args], capture_output=True, text=True, cwd=cwd, timeout=timeout)


def render_mp4(project: Path, out: Path, quality: str = "standard") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = _npx("render", str(project), "-o", str(out), "-q", quality, "--quiet")
    if r.returncode != 0 or not out.exists():
        raise RenderError(f"hyperframes render failed: {(r.stderr or r.stdout)[-800:]}")
    return out


THUMB = """<!doctype html><html lang="en"><head><meta charset="UTF-8" />
<style>
@font-face {{ font-family: "Archivo Black"; src: url("assets/fonts/ArchivoBlack-400.woff2") format("woff2"); }}
@font-face {{ font-family: "Archivo"; src: url("assets/fonts/Archivo-700.woff2") format("woff2"); font-weight: 700; }}
body {{ margin: 0; background: #16133F; }}
#root {{ position: relative; width: 100%; height: 100%; overflow: hidden; background: #16133F; }}
img {{ position: absolute; left: 0; top: 0; width: 560px; height: 720px; object-fit: cover; }}
.txt {{ position: absolute; left: 608px; right: 48px; top: 0; bottom: 0; display: flex; flex-direction: column; justify-content: center; gap: 22px; }}
.pill {{ align-self: flex-start; background: #FF3E7F; color: #fff; font: 700 30px Archivo, sans-serif; padding: 8px 22px; border-radius: 999px; }}
.h {{ font: 400 {size}px/0.98 "Archivo Black", sans-serif; color: #FFD23F; }}
.s {{ font: 700 36px Archivo, sans-serif; color: #fff; }}
.q {{ position: absolute; right: 26px; top: -30px; font: 400 300px/1 "Archivo Black", sans-serif; color: #FF3E7F; opacity: .9; }}
.logo {{ position: absolute; left: 24px; bottom: 24px; width: 72px; height: 72px; border-radius: 16px; background: #FFD23F; color: #16133F; font: 400 54px/72px "Archivo Black", sans-serif; text-align: center; }}
</style></head><body>
<div id="root" data-composition-id="thumb" data-start="0" data-width="1280" data-height="720" data-duration="1">
<img src="assets/{image}" alt="" /><div class="q">?</div>
<div class="txt"><div class="pill">FAMILY QUIZ NIGHT #{episode}</div><div class="h">{headline}</div><div class="s">Can you beat your kids?</div></div>
<div class="logo">?</div></div>
<script src="assets/gsap.min.js"></script>
<script>window.__timelines["thumb"] = gsap.timeline({{ paused: true }});</script>
</body></html>"""


def render_thumbnails(headlines: list[str], images: list[Path], episode: int, outdir: Path) -> list[Path]:
    """Three 1280x720 PNG variants for YouTube Test & Compare (spec §6.4)."""
    outs = []
    for k, (headline, image) in enumerate(zip(headlines, images)):
        proj = outdir / f"thumb{k}"
        (proj / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
        for f in (TEMPLATES / "fonts").glob("*.woff2"):
            shutil.copy2(f, proj / "assets" / "fonts" / f.name)
        shutil.copy2(image, proj / "assets" / image.name)
        shutil.copy2(TEMPLATES / "vendor" / "gsap.min.js", proj / "assets" / "gsap.min.js")
        size = 92 if len(headline) <= 22 else 72
        html = THUMB.format(image=image.name, episode=episode, headline=headline.upper(), size=size)
        (proj / "index.html").write_text(html)
        shots = proj / "shots"
        r = _npx("snapshot", str(proj), "--at", "0.5", "--no-end", "-o", str(shots), "--describe", "false", timeout=600)
        pngs = sorted(shots.glob("*.png"))
        if r.returncode != 0 or not pngs:
            raise RenderError(f"thumbnail snapshot failed: {(r.stderr or r.stdout)[-500:]}")
        dest = outdir / f"thumb_{k}.png"
        shutil.copy2(pngs[0], dest)
        outs.append(dest)
    return outs
