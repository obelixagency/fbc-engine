"""Weekly batch generation (spec §5): 14 Shorts + 1 long video, deduped, copy-checked, fact-checked."""
from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .dedupe import is_duplicate
from .gemini import generate_json
from .models import (Batch, BatchError, KIND, FORMATS, PER_ROUND, batch_from_obj, question_problems,
                     short_problems)
from .verify import verify_questions
from .weights import pick_category, pick_formats

ROOT = Path(__file__).resolve().parent.parent
SHORT_PROMPT = (ROOT / "prompts" / "generate_short.md").read_text()
LONG_PROMPT = (ROOT / "prompts" / "generate_long.md").read_text()
HOOKS = json.loads((ROOT / "prompts" / "hooks.json").read_text())
ROUND_NAMES = ["Animals", "Around the World", "Science & Space", "Food & Body", "Brain Teasers"]
N_SHORTS = 14
EXTRA = 0.2

Q_SCHEMA = {"type": "object", "properties": {
    "category": {"type": "string"}, "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]}, "question": {"type": "string"},
    "options": {"type": "array", "items": {"type": "string"}}, "answer_index": {"type": "integer"},
    "answer": {"type": "string"}, "fun_fact": {"type": "string"}, "source_url": {"type": "string"},
    "visual_query": {"type": "string"}},
    "required": ["category", "difficulty", "question", "options", "answer_index", "answer", "fun_fact", "visual_query"]}
SHORTS_SCHEMA = {"type": "array", "items": {"type": "object", "properties": {
    "format": {"type": "string"}, "questions": {"type": "array", "items": Q_SCHEMA},
    "titles": {"type": "array", "items": {"type": "string"}}, "description": {"type": "string"},
    "first_comment": {"type": "string"}}, "required": ["format", "questions", "titles", "description", "first_comment"]}}
LONG_SCHEMA = {"type": "object", "properties": {
    "rounds": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "questions": {"type": "array", "items": Q_SCHEMA}}}},
    "bonus": {"type": "array", "items": Q_SCHEMA}, "titles": {"type": "array", "items": {"type": "string"}},
    "description": {"type": "string"}, "first_comment": {"type": "string"}},
    "required": ["rounds", "bonus", "titles", "description", "first_comment"]}


def _norm(qd: dict) -> dict:
    """Small, safe clean-ups of model output before validation (e.g. " Easy " -> "easy")."""
    d = qd.get("difficulty")
    return dict(qd, difficulty=d.strip().lower()) if isinstance(d, str) else dict(qd)


def _wk(week: str) -> str:
    return week.lower().replace("-", "")


def _avoid(used: list[dict], n: int = 60) -> str:
    return "\n".join(f"- {u['question']}" for u in used[-n:]) or "- (none)"


def _pick_hook(fmt: str, weights: dict, rng: random.Random) -> dict:
    pool = [h for h in HOOKS if fmt in h["formats"]]
    hw = weights.get("hooks", {})
    return rng.choices(pool, weights=[hw.get(h["id"], 1 / len(HOOKS)) for h in pool])[0]


def _fresh(qd: dict, used: list[dict], seen: set, today: date) -> bool:
    key = (qd.get("question", "").lower(), qd.get("answer", "").lower())
    if key in seen or is_duplicate(qd.get("question", ""), qd.get("answer", ""), used, today=today):
        return False
    seen.add(key)
    return True


def _shorts(formats, weights, used, rng, gen, today, seen) -> list[dict]:
    plan = [(f, pick_category(weights, rng)) for f in formats]
    extra = [(rng.choice(formats), pick_category(weights, rng)) for _ in range(round(EXTRA * len(formats)) or 1)]
    items = "\n".join(f"- {f}, {c}" for f, c in plan + extra)
    raw = gen(SHORT_PROMPT.format(items=items, avoid=_avoid(used)), SHORTS_SCHEMA) or []
    kept: list[dict] = []
    for cand in raw:
        fmt = cand.get("format")
        if fmt not in FORMATS:
            continue
        cand = dict(cand)
        hook = _pick_hook(fmt, weights, rng)
        cand.update(id="", hook=hook["text"], hook_id=hook["id"], chosen_title_index=0)
        cand["questions"] = [dict(_norm(qd), source_url=qd.get("source_url", ""), verified=False, flags=[])
                             for qd in cand.get("questions", [])]
        kept.append(cand)
    return kept


def _assign_and_filter(cands: list[dict], week: str, used, today, seen, n: int,
                       out: list[dict] | None = None, rejects: list[str] | None = None) -> list[dict]:
    out = [] if out is None else out
    rejects = [] if rejects is None else rejects
    for cand in cands:
        if len(out) == n:
            break
        idx = len(out)
        sid = f"s_{_wk(week)}_{idx:02d}"
        cand["id"] = sid
        for k, qd in enumerate(cand.get("questions", [])):
            qd["id"] = f"q_{_wk(week)}_{idx:02d}_{k}"
        problems = short_problems(cand)
        if problems:
            rejects.extend(problems)
            continue
        if cand["format"] != "would_you_rather":
            snapshot = set(seen)
            if not all(_fresh(qd, used, seen, today) for qd in cand["questions"]):
                seen.clear(); seen.update(snapshot)
                rejects.append(f"{sid}: duplicate of a recent question")
                continue
        out.append(cand)
    return out


def _long(week, episode, used, gen, today, seen) -> dict:
    raw = gen(LONG_PROMPT.format(episode=episode, rounds=", ".join(ROUND_NAMES), avoid=_avoid(used)), LONG_SCHEMA) or {}
    lid = f"l_{_wk(week)}"
    rounds = []
    for r_i, name in enumerate(ROUND_NAMES, 1):
        src = next((r for r in raw.get("rounds", []) if r.get("name") == name), None) or \
            (raw.get("rounds", [])[r_i - 1] if len(raw.get("rounds", [])) >= r_i else {"questions": []})
        qs, rejects = [], []
        for qd in src.get("questions", []):
            qd = dict(_norm(qd), id=f"{lid}_r{r_i}_{len(qs)}", source_url=qd.get("source_url", ""), verified=False, flags=[])
            problems = question_problems(qd)
            if problems:
                rejects.extend(problems)
            elif _fresh(qd, used, seen, today):
                qs.append(qd)
            else:
                rejects.append(f"{qd['id']}: duplicate of a recent question")
            if len(qs) == PER_ROUND:
                break
        if len(qs) < PER_ROUND:
            raise BatchError([f"{lid}: round {name!r} has only {len(qs)} usable questions", *rejects[:20]])
        rounds.append({"name": name, "questions": qs})
    bonus = None
    for qd in raw.get("bonus", []):
        qd = dict(_norm(qd), id=f"{lid}_bonus", source_url=qd.get("source_url", ""), verified=False, flags=[])
        if not question_problems(qd) and _fresh(qd, used, seen, today):
            bonus = qd
            break
    if bonus is None:
        raise BatchError([f"{lid}: no usable bonus question"])
    return {"id": lid, "episode": episode, "rounds": rounds, "bonus": bonus,
            "titles": raw.get("titles", [])[:3], "chosen_title_index": 0,
            "description": raw.get("description", ""), "first_comment": raw.get("first_comment", ""), "chapters": []}


def build_batch(week: str, weights: dict, used: list[dict], episode: int, rng: random.Random,
                gen=generate_json, today: date | None = None, report_md: str = "") -> Batch:
    today = today or date.today()
    seen: set = set()
    formats = pick_formats(weights, N_SHORTS, rng)
    rejects: list[str] = []
    cands = _shorts(formats, weights, used, rng, gen, today, seen)
    shorts = _assign_and_filter(cands, week, used, today, seen, N_SHORTS, rejects=rejects)
    if len(shorts) < N_SHORTS:   # one top-up call for the formats that are still missing
        have = [x["format"] for x in shorts]
        missing = list(formats)
        for f in have:
            if f in missing:
                missing.remove(f)
        cands = _shorts(missing[:N_SHORTS - len(shorts)] or formats[:1], weights, used, rng, gen, today, seen)
        _assign_and_filter(cands, week, used, today, seen, N_SHORTS, out=shorts, rejects=rejects)
    if len(shorts) < N_SHORTS:
        raise BatchError([f"only {len(shorts)} of {N_SHORTS} Shorts survived validation and de-duplication",
                          *rejects[:40]])
    long = _long(week, episode, used, gen, today, seen)
    batch = batch_from_obj({"week": week, "shorts": shorts, "long": long, "report_md": report_md})
    to_check = [q for s in batch.shorts if s.format != "would_you_rather" for q in s.questions]
    verify_questions(to_check + batch.long.all_questions(), gen=gen)
    return batch
