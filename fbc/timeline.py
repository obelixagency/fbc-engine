"""Turns an item + measured voice durations + background clips into an absolute-time timeline
(spec §6.2 retention rules, §7 formats). The timing is built from the real audio lengths."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import LongItem, ShortItem


class TimelineError(RuntimeError):
    pass


@dataclass
class Segment:
    kind: str
    start: float
    end: float
    data: dict = field(default_factory=dict)
    audio: list = field(default_factory=list)   # (file name, absolute time)
    sfx: list = field(default_factory=list)     # (file name, absolute time)
    clip: dict | None = None                    # {"name", "kind", "portrait"}


@dataclass
class Timeline:
    width: int
    height: int
    duration: float
    segments: list[Segment]
    fps: int = 30
    chapters: list[str] = field(default_factory=list)
    loop_safe: bool = True

    def countdown_windows(self) -> list[tuple[float, float]]:
        return [(s.data["countdown_start"], s.data["countdown_start"] + s.data["countdown"])
                for s in self.segments if s.data.get("countdown")]


OUTRO_TEXT = {"three_q": "Comment your score! 👇", "zoom_reveal": "Did you guess it? 👇",
              "riddle": "Did you solve it? 👇", "parents_vs_kids": "Who won at your house? 👇",
              "true_false": "Comment your score! 👇", "would_you_rather": "A or B? Vote below! 👇"}
SHORT_MAX = 35.0
MIN_SHORT = {"three_q": 22.0, "parents_vs_kids": 22.0, "true_false": 22.0,
             "zoom_reveal": 12.0, "riddle": 12.0, "would_you_rather": 12.0}
COUNTDOWN = {"three_q": 3.0, "parents_vs_kids": 3.0, "true_false": 2.0, "zoom_reveal": 5.0, "riddle": 5.0}
SEG_KIND = {"three_q": "mcq", "parents_vs_kids": "mcq", "true_false": "tf", "zoom_reveal": "zoom",
            "riddle": "riddle", "would_you_rather": "wyr"}
MIN_COUNTDOWN = 2.0
READ_PAD, REVEAL_PAD, OUTRO_MAX = 0.25, 0.5, 3.0
WYR_VOTE = 5.0


def _question_segment(kind, q, t, qd, rd, countdown, reveal_extra, clip, n, total, label=""):
    read_end = t + qd + READ_PAD
    seg = Segment(kind, t, 0, clip=clip)
    seg.audio.append((f"{q['line']}.wav", t))
    seg.data = {"question": q["q"].question, "options": q["q"].options, "answer_index": q["q"].answer_index,
                "answer": q["q"].answer, "fun_fact": q["q"].fun_fact, "n": n, "total": total, "label": label,
                "difficulty": q["q"].difficulty}
    if kind == "wyr":
        seg.data.update(countdown=0, countdown_start=read_end, reveal_at=None)
        seg.end = read_end + WYR_VOTE
        return seg
    whole = int(countdown)
    seg.data.update(countdown_start=read_end, countdown=countdown, reveal_at=read_end + countdown)
    seg.sfx += [("tick.wav", read_end + k) for k in range(whole)]
    reveal_at = read_end + countdown
    seg.sfx.append(("ding.wav", reveal_at))
    seg.audio.append((f"{q['line']}_reveal.wav", reveal_at + 0.1))
    seg.end = reveal_at + 0.1 + rd + REVEAL_PAD + reveal_extra
    return seg


def _short_layout(item: ShortItem, d: dict, clips: dict, countdown: float, reveal_extra: float, outro_extra: float):
    kind = SEG_KIND[item.format]
    first_clip = clips.get(item.questions[0].id)
    segs = [Segment("hook", 0.0, d["hook"] + 0.15, data={"text": item.hook}, audio=[("hook.wav", 0.0)],
                    sfx=[("whoosh.wav", 0.0)], clip=first_clip)]
    t = segs[0].end
    total = len(item.questions)
    for n, q in enumerate(item.questions, 1):
        label = ""
        if item.format == "parents_vs_kids":
            label = "PARENTS" if n == 1 else "KIDS"
        rd = d.get(f"q{n}_reveal", 0.0)
        seg = _question_segment(kind, {"q": q, "line": f"q{n}"}, t, d[f"q{n}"], rd, countdown, reveal_extra,
                                clips.get(q.id), n, total, label)
        segs.append(seg)
        t = seg.end
    outro_len = min(d["outro"] + 0.3, OUTRO_MAX) + outro_extra
    segs.append(Segment("outro", t, t + outro_len, data={"outro_text": OUTRO_TEXT[item.format], "hook": item.hook},
                        audio=[("outro.wav", t)],
                        clip=first_clip))
    return segs, t + outro_len


def build_short_timeline(item: ShortItem, durations: dict, clips: dict) -> Timeline:
    lo, hi = MIN_SHORT[item.format], SHORT_MAX
    base = COUNTDOWN.get(item.format, 0.0)
    c = base
    segs, total = _short_layout(item, durations, clips, c, 0.0, 0.0)
    while total > hi and c > MIN_COUNTDOWN:
        c = max(MIN_COUNTDOWN, c - 0.5)
        segs, total = _short_layout(item, durations, clips, c, 0.0, 0.0)
    if total > hi:
        raise TimelineError(f"{item.id}: {total:.1f}s even with {MIN_COUNTDOWN}s countdowns (max {hi}s)")
    if total < lo and base:
        while total < lo and c < base + 2:
            c += 0.5
            segs, total = _short_layout(item, durations, clips, c, 0.0, 0.0)
    if total < lo:
        n_reveals = sum(1 for s in segs if s.data.get("reveal_at"))
        extra = min(1.5, (lo - total) / n_reveals) if n_reveals else 0.0
        segs, total = _short_layout(item, durations, clips, c, extra, 0.0)
    if total < lo:
        segs, total = _short_layout(item, durations, clips, c, extra if n_reveals else 0.0, lo - total)
    return Timeline(1080, 1920, round(total, 3), segs)


LONG_MIN, LONG_MAX = 480.0, 720.0
LONG_COUNTDOWN, LONG_MIN_CD, LONG_MAX_CD = 7.0, 5.0, 10.0
TIERS = [("Rookie", 0, 10), ("Brainiac", 11, 22), ("Family Champion", 23, 31)]
END_SCREEN = 20.0


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def parse_mmss(s: str) -> int:
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


def _long_layout(item: LongItem, d: dict, clips: dict, c: float):
    segs, chapters = [], ["0:00 Intro"]
    first = clips.get(item.rounds[0]["questions"][0].id)
    intro_len = max(d["intro"] + 0.6, 10.0)  # >= 10 s so the first chapter is valid on YouTube
    segs.append(Segment("intro", 0.0, intro_len, data={"episode": item.episode}, audio=[("intro.wav", 0.0)],
                        sfx=[("whoosh.wav", 0.0)], clip=first))
    t = intro_len
    for r, rnd in enumerate(item.rounds, 1):
        chapters.append(f"{mmss(t)} Round {r}: {rnd['name']}")
        title_len = d[f"r{r}_title"] + 0.8
        segs.append(Segment("round_title", t, t + title_len, data={"round": r, "name": rnd["name"]},
                            audio=[(f"r{r}_title.wav", t)], sfx=[("whoosh.wav", t)], clip=clips.get(rnd["questions"][0].id)))
        t += title_len
        for n, q in enumerate(rnd["questions"], 1):
            seg = _question_segment("mcq", {"q": q, "line": f"r{r}q{n}"}, t, d[f"r{r}q{n}"], d[f"r{r}q{n}_reveal"],
                                    c, 0.0, clips.get(q.id), n, len(rnd["questions"]), f"ROUND {r}")
            segs.append(seg)
            t = seg.end
        score_len = d[f"r{r}_score"] + 3.0
        segs.append(Segment("score", t, t + score_len, data={"round": r}, audio=[(f"r{r}_score.wav", t)],
                            clip=clips.get(rnd["questions"][-1].id)))
        t += score_len
    chapters.append(f"{mmss(t)} Bonus")
    seg = _question_segment("mcq", {"q": item.bonus, "line": "bonus"}, t, d["bonus"], d["bonus_reveal"], c, 0.0,
                            clips.get(item.bonus.id), 1, 1, "BONUS")
    segs.append(seg)
    t = seg.end
    tier_len = 6.0
    segs.append(Segment("tiers", t, t + tier_len, data={"tiers": TIERS}, clip=clips.get(item.bonus.id)))
    t += tier_len
    end_len = max(END_SCREEN, d["outro"] + 1.0)
    segs.append(Segment("end", t, t + end_len, data={"title": item.title}, audio=[("outro.wav", t)], clip=first))
    return segs, t + end_len, chapters


def build_long_timeline(item: LongItem, durations: dict, clips: dict) -> Timeline:
    c = LONG_COUNTDOWN
    segs, total, chapters = _long_layout(item, durations, clips, c)
    while total < LONG_MIN and c < LONG_MAX_CD:
        c += 0.5
        segs, total, chapters = _long_layout(item, durations, clips, c)
    while total > LONG_MAX and c > LONG_MIN_CD:
        c -= 0.5
        segs, total, chapters = _long_layout(item, durations, clips, c)
    if not LONG_MIN - 0.01 <= total <= LONG_MAX:
        raise TimelineError(f"{item.id}: long video is {total:.0f}s (needs {LONG_MIN:.0f}-{LONG_MAX:.0f}s)")
    return Timeline(1920, 1080, round(total, 3), segs, chapters=chapters, loop_safe=False)
