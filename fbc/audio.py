"""Voice-over: build audio_request.json (spec §8 shape) per video and synthesize it line by
line with Gemini TTS. Synthesis is resumable: finished lines are kept, so a quota stop
mid-week continues on the next tick."""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

from .config import HOST_STYLE, HOST_VOICE, REVEAL_STYLE, REVEAL_VOICE, TTS_MODEL
from .gemini import tts_wav
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


def synthesize(req: dict, outdir: Path, tts=tts_wav) -> dict[str, float]:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "audio_request.json").write_text(json.dumps(req, indent=2, ensure_ascii=False))
    durations = {}
    for line in req["lines"]:
        path = outdir / f"{line['id']}.wav"
        if not path.exists():
            data = tts(line["text"], line.get("voice", req["default_voice"]), line.get("style", req["default_style"]))
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.rename(path)
        durations[line["id"]] = wav_duration(path)
    return durations


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
