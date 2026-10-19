"""Voice-over: build audio_request.json (spec §8 shape) per video and synthesize it line by
line with Gemini TTS. Synthesis is resumable: finished lines are kept, so a quota stop
mid-week continues on the next tick."""
from __future__ import annotations

import json
import re
import subprocess
import wave
from pathlib import Path

from .config import HOST_STYLE, HOST_VOICE, REVEAL_STYLE, REVEAL_VOICE, TTS_MODEL
from .gemini import QuotaExceeded, tts_wav
from .models import LongItem, ShortItem

OUTROS = {
    "three_q": "How many did you get? Comment your score!",
    "zoom_reveal": "Did you guess it? Tell us in the comments!",
    "riddle": "Did you solve it? Comment below!",
    "parents_vs_kids": "Who won at your house? Parents or kids?",
    "true_false": "How many did you get right? Comment your score!",
    "would_you_rather": "Which one would you pick? Vote in the comments!",
}


def _line(lid: str, text: str, **extra) -> dict:
    return {"id": lid, "text": text, "language": "en-US", **extra}


def _reveal(q) -> str:
    text = f"It's {q.answer}!"
    return f"{text} {q.fun_fact}".strip() if q.fun_fact else text


def _short_lines(s: ShortItem) -> list[dict]:
    lines = [_line("hook", s.hook)]
    for n, q in enumerate(s.questions, 1):
        if s.format == "would_you_rather":
            a, b = q.options
            lines.append(_line(f"q{n}", f"Would you rather {a.rstrip('?.').lower()}, or {b.rstrip('?.').lower()}?"))
            continue
        spoken = q.question
        if s.format == "parents_vs_kids":
            spoken = f"{'Parents' if n == 1 else 'Kids'}, this one's for you! {q.question}"
        lines.append(_line(f"q{n}", spoken))
        reveal = f"{q.answer}!" if s.format == "true_false" else _reveal(q)
        lines.append(_line(f"q{n}_reveal", reveal, style=REVEAL_STYLE))
    lines.append(_line("outro", OUTROS[s.format]))
    return lines


def _long_lines(item: LongItem) -> list[dict]:
    lines = [_line("intro", f"Welcome to Family Quiz Night number {item.episode}! Grab a pen, everyone keeps "
                            "their own score. Thirty questions, five seconds each. Let's go!")]
    for r, rnd in enumerate(item.rounds, 1):
        lines.append(_line(f"r{r}_title", f"Round {r}: {rnd['name']}!"))
        for n, q in enumerate(rnd["questions"], 1):
            lines.append(_line(f"r{r}q{n}", q.question))
            lines.append(_line(f"r{r}q{n}_reveal", _reveal(q), voice=REVEAL_VOICE, style=REVEAL_STYLE))
        lines.append(_line(f"r{r}_score", f"That's the end of round {r}. How many did you get?"))
    lines.append(_line("bonus", f"Bonus question! Worth one extra point. {item.bonus.question}"))
    lines.append(_line("bonus_reveal", _reveal(item.bonus), voice=REVEAL_VOICE, style=REVEAL_STYLE))
    lines.append(_line("outro", "Add up your points! Who's the family champion? Tell us your scores in the "
                                "comments, and subscribe for a new quiz night every Saturday!"))
    return lines


def build_audio_request(item: ShortItem | LongItem) -> dict:
    lines = _long_lines(item) if isinstance(item, LongItem) else _short_lines(item)
    return {"model": TTS_MODEL, "video_id": item.id, "default_voice": HOST_VOICE,
            "default_style": HOST_STYLE, "lines": lines}


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


SEP = "\n\n...\n\n"
CHUNK = 12                      # lines per TTS call
TTS_MODELS = (TTS_MODEL, "gemini-3.1-flash-tts-preview")   # each has its own free daily quota
PAUSE_HINT = " Leave a clear two-second pause between lines."
MIN_GAP_S = 0.45


def _silences(path: Path) -> list[tuple[float, float]]:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"silencedetect=noise=-40dB:d={MIN_GAP_S}", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    return list(zip(starts, ends))


def _cut(src: Path, dest: Path, t0: float, t1: float | None) -> None:
    trim = f"atrim=start={t0:.3f}" + (f":end={t1:.3f}" if t1 is not None else "")
    strip = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08"
    af = f"{trim},asetpts=PTS-STARTPTS,{strip},areverse,{strip},areverse,apad=pad_dur=0.12"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", af, "-ar", "24000", "-ac", "1",
                    str(dest)], check=True)


def tighten(path: Path, keep_s: float = 0.35) -> None:
    """Shorten any pause inside a line to keep_s (Gemini sometimes adds a dramatic 1-2 s pause, which QA
    rightly rejects). Idempotent, so it is safe to run on cached lines every tick."""
    tmp = path.with_suffix(".tight.wav")
    af = (f"silenceremove=stop_periods=-1:stop_duration={keep_s + 0.25:.2f}:stop_threshold=-45dB"
          f":stop_silence={keep_s:.2f}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-af", af, "-ar", "24000", "-ac", "1",
                    str(tmp)], check=True)
    if wav_duration(tmp) > 0.3:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)


def split_at_pauses(wav: Path, n: int, dests: list[Path]) -> bool:
    """Cut a batched take into n pieces at its n-1 longest pauses. False if it cannot be split cleanly."""
    if n == 1:
        _cut(wav, dests[0], 0.0, None)
        return wav_duration(dests[0]) > 0.3
    total = wav_duration(wav)
    gaps = [(e - s, s, e) for s, e in _silences(wav) if s > 0.05 and e < total - 0.05]
    if len(gaps) < n - 1:
        return False
    cuts = sorted(sorted(gaps, reverse=True)[: n - 1], key=lambda g: g[1])
    bounds = [0.0] + [(s + e) / 2 for _, s, e in cuts] + [None]
    for k, dest in enumerate(dests):
        _cut(wav, dest, bounds[k], bounds[k + 1])
        if wav_duration(dest) < 0.3:
            return False
    return True


def _tts_any(tts, text: str, voice: str, style: str) -> bytes:
    last = None
    for model in TTS_MODELS:
        try:
            return tts(text, voice, style, model=model)
        except QuotaExceeded as e:   # this model's daily quota is used up: try the next one
            last = e
    raise last


def _groups(req: dict, missing: list[dict]) -> list[tuple[str, list[dict]]]:
    by_voice: dict[str, list[dict]] = {}
    for line in missing:
        by_voice.setdefault(line.get("voice", req["default_voice"]), []).append(line)
    return [(v, ls[i:i + CHUNK]) for v, ls in by_voice.items() for i in range(0, len(ls), CHUNK)]


def synthesize(req: dict, outdir: Path, tts=tts_wav, spacing_s: float = 21.0) -> dict[str, float]:
    """Voice every line of req into outdir/<line id>.wav, a few lines per TTS call (free tier allows
    ~10 calls/day per model). Finished lines are kept, so a quota stop resumes next tick."""
    import time
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "audio_request.json").write_text(json.dumps(req, indent=2, ensure_ascii=False))
    missing = [l for l in req["lines"] if not (outdir / f"{l['id']}.wav").exists()]
    first = True
    for voice, lines in _groups(req, missing):
        if not first and spacing_s:
            time.sleep(spacing_s)    # free tier: 3 requests/minute
        first = False
        style = req["default_style"] + PAUSE_HINT
        take = outdir / f"_take_{lines[0]['id']}.wav"
        take.write_bytes(_tts_any(tts, SEP.join(l["text"] for l in lines), voice, style))
        dests = [outdir / f"{l['id']}.part.wav" for l in lines]
        ok = split_at_pauses(take, len(lines), dests)
        take.unlink()
        if ok:
            for l, d in zip(lines, dests):
                d.rename(outdir / f"{l['id']}.wav")
            continue
        for d in dests:
            d.unlink(missing_ok=True)
        for l in lines:   # the take could not be split cleanly: voice these lines one by one
            if spacing_s:
                time.sleep(spacing_s)
            data = _tts_any(tts, l["text"], l.get("voice", voice), l.get("style", req["default_style"]))
            part = outdir / f"{l['id']}.part.wav"
            part.write_bytes(data)
            part.rename(outdir / f"{l['id']}.wav")
    for l in req["lines"]:
        tighten(outdir / f"{l['id']}.wav")
    return {l["id"]: wav_duration(outdir / f"{l['id']}.wav") for l in req["lines"]}


SFX = {
    "tick.wav": ["-f", "lavfi", "-i", "sine=frequency=1000:duration=0.04", "-af", "afade=t=out:st=0.02:d=0.02,volume=0.6"],
    "ding.wav": ["-f", "lavfi", "-i", "sine=frequency=880:duration=0.35", "-f", "lavfi", "-i",
                 "sine=frequency=1320:duration=0.35", "-filter_complex",
                 "[1]adelay=90|90[b];[0][b]amix=inputs=2:normalize=0,afade=t=out:st=0.15:d=0.2,volume=0.5"],
    "whoosh.wav": ["-f", "lavfi", "-i", "anoisesrc=color=pink:duration=0.3", "-af",
                   "highpass=f=800,lowpass=f=6000,afade=t=in:d=0.1,afade=t=out:st=0.15:d=0.15,volume=0.4"],
}


def make_sfx(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for name, args in SFX.items():
        dest = outdir / name
        if not dest.exists():
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-ar", "44100", "-ac", "2", str(dest)], check=True)
