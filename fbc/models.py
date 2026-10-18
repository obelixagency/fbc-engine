"""Batch data model and validation. The batch is what Mohamed reviews and edits in the
weekly PR (spec §5), so validation reports every problem in plain words."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, fields

from .config import LIMITS
from .copy_rules import check_copy

FORMATS = {"three_q": 3, "zoom_reveal": 1, "riddle": 1, "parents_vs_kids": 2,
           "true_false": 5, "would_you_rather": 1}
DIFFICULTIES = {"easy", "medium", "hard"}
ROUNDS, PER_ROUND = 5, 6
REQUIRED_Q = ["id", "category", "difficulty", "question", "options", "answer_index", "answer",
              "fun_fact", "source_url", "visual_query"]


class BatchError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Question:
    id: str
    category: str
    difficulty: str
    question: str
    options: list[str]
    answer_index: int
    answer: str
    fun_fact: str
    source_url: str
    visual_query: str
    verified: bool = False
    flags: list[str] = field(default_factory=list)


@dataclass
class ShortItem:
    id: str
    format: str
    hook: str
    questions: list[Question]
    titles: list[str]
    description: str
    first_comment: str
    chosen_title_index: int = 0
    hook_id: str = ""

    @property
    def title(self) -> str:
        return self.titles[self.chosen_title_index]


@dataclass
class LongItem:
    id: str
    episode: int
    rounds: list[dict]          # {"name": str, "questions": list[Question]}
    bonus: Question
    titles: list[str]
    description: str
    first_comment: str
    chosen_title_index: int = 0
    chapters: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.titles[self.chosen_title_index]

    def all_questions(self) -> list[Question]:
        return [q for r in self.rounds for q in r["questions"]] + [self.bonus]


@dataclass
class Batch:
    week: str
    shorts: list[ShortItem]
    long: LongItem
    report_md: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    def all_questions(self) -> list[Question]:
        return [q for s in self.shorts for q in s.questions] + self.long.all_questions()


def _words(text: str) -> int:
    return len((text or "").split())


def _question(d: dict, kind: str, problems: list[str]) -> Question | None:
    qid = d.get("id", "?")
    missing = [k for k in REQUIRED_Q if k not in d]
    if kind == "wyr":
        missing = [k for k in missing if k not in ("fun_fact", "source_url", "answer")]
    for k in missing:
        problems.append(f"{qid}: missing {k}")
    if missing:
        return None
    known = {f.name for f in fields(Question)}
    q = Question(**{k: v for k, v in d.items() if k in known} | {
        "fun_fact": d.get("fun_fact", ""), "source_url": d.get("source_url", ""), "answer": d.get("answer", "")})
    max_q = 20 if kind == "riddle" else LIMITS["question_words"]
    if _words(q.question) > max_q:
        problems.append(f"{qid}: question has {_words(q.question)} words (max {max_q})")
    if q.difficulty not in DIFFICULTIES:
        problems.append(f"{qid}: difficulty must be easy, medium or hard")
    if q.fun_fact and _words(q.fun_fact) > LIMITS["fact_words"]:
        problems.append(f"{qid}: fun_fact has {_words(q.fun_fact)} words (max {LIMITS['fact_words']})")
    max_opt = 6 if kind == "wyr" else LIMITS["option_words"]
    for o in q.options:
        if _words(o) > max_opt:
            problems.append(f'{qid}: option "{o}" has {_words(o)} words (max {max_opt})')
    if kind == "mcq":
        if len(q.options) != 3:
            problems.append(f"{qid}: needs exactly 3 options")
        elif not 0 <= q.answer_index < 3:
            problems.append(f"{qid}: answer_index must be 0, 1 or 2")
        elif q.answer != q.options[q.answer_index]:
            problems.append(f"{qid}: answer must equal the option at answer_index")
    elif kind == "tf":
        if q.options != ["True", "False"] or q.answer_index not in (0, 1):
            problems.append(f'{qid}: true/false needs options ["True","False"] and answer_index 0 or 1')
    elif kind == "riddle":
        if not q.answer:
            problems.append(f"{qid}: riddle needs an answer")
    elif kind == "zoom":
        if q.options and len(q.options) != 3:
            problems.append(f"{qid}: zoom reveal needs 0 or 3 options")
        if not q.answer:
            problems.append(f"{qid}: zoom reveal needs an answer")
    elif kind == "wyr":
        if len(q.options) != 2:
            problems.append(f"{qid}: would-you-rather needs exactly 2 options")
    return q


KIND = {"three_q": "mcq", "parents_vs_kids": "mcq", "zoom_reveal": "zoom", "riddle": "riddle",
        "true_false": "tf", "would_you_rather": "wyr"}


def _copy_checks(item_id: str, texts: list[str], problems: list[str]) -> None:
    for t in texts:
        for p in check_copy(t):
            problems.append(f"{item_id}: {p}")


def _titles(item_id: str, titles, idx, problems):
    if not titles:
        problems.append(f"{item_id}: needs at least one title")
        return
    if not 0 <= idx < len(titles):
        problems.append(f"{item_id}: chosen_title_index out of range")
    for t in titles:
        if len(t) > LIMITS["title_chars"]:
            problems.append(f'{item_id}: title "{t}" is {len(t)} chars (max {LIMITS["title_chars"]})')


def parse_batch(raw: str) -> Batch:
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BatchError([f"batch.json is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}"])
    problems: list[str] = []
    shorts: list[ShortItem] = []
    for s in d.get("shorts", []):
        sid, fmt = s.get("id", "?"), s.get("format")
        if fmt not in FORMATS:
            problems.append(f"{sid}: unknown format {fmt!r}")
            continue
        qs = [_question(x, KIND[fmt], problems) for x in s.get("questions", [])]
        if len(qs) != FORMATS[fmt]:
            problems.append(f"{sid}: {fmt} needs {FORMATS[fmt]} questions (has {len(qs)})")
        _titles(sid, s.get("titles"), s.get("chosen_title_index", 0), problems)
        _copy_checks(sid, [s.get("hook", ""), s.get("description", ""), s.get("first_comment", ""), *s.get("titles", [])], problems)
        if None not in qs:
            shorts.append(ShortItem(id=sid, format=fmt, hook=s.get("hook", ""), questions=qs,
                                    titles=s.get("titles", []), description=s.get("description", ""),
                                    first_comment=s.get("first_comment", ""),
                                    chosen_title_index=s.get("chosen_title_index", 0), hook_id=s.get("hook_id", "")))
    if not d.get("shorts"):
        problems.append("batch has no shorts")
    ld = d.get("long") or {}
    lid = ld.get("id", "long")
    rounds = []
    if len(ld.get("rounds", [])) != ROUNDS:
        problems.append(f"{lid}: needs {ROUNDS} rounds")
    for r in ld.get("rounds", []):
        rq = [_question(x, "mcq", problems) for x in r.get("questions", [])]
        if len(rq) != PER_ROUND:
            problems.append(f"{lid}: round {r.get('name')!r} needs {PER_ROUND} questions")
        rounds.append({"name": r.get("name", ""), "questions": rq})
    bonus = _question(ld.get("bonus") or {"id": f"{lid}_bonus"}, "mcq", problems)
    _titles(lid, ld.get("titles"), ld.get("chosen_title_index", 0), problems)
    _copy_checks(lid, [ld.get("description", ""), ld.get("first_comment", ""), *ld.get("titles", [])], problems)
    if problems:
        raise BatchError(problems)
    long = LongItem(id=lid, episode=int(ld.get("episode", 1)), rounds=rounds, bonus=bonus,
                    titles=ld["titles"], description=ld.get("description", ""), first_comment=ld.get("first_comment", ""),
                    chosen_title_index=ld.get("chosen_title_index", 0), chapters=ld.get("chapters", []))
    return Batch(week=d.get("week", ""), shorts=shorts, long=long, report_md=d.get("report_md", ""))


def batch_from_obj(obj) -> Batch:
    """Validate an in-memory batch (e.g. freshly generated) through the same rules."""
    return parse_batch(json.dumps(obj if isinstance(obj, dict) else asdict(obj)))
