import copy
import pytest

FORMAT_SIZES = {"three_q": 3, "zoom_reveal": 1, "riddle": 1, "parents_vs_kids": 2,
                "true_false": 5, "would_you_rather": 1}


def q(qid, fmt="mcq", n=0):
    base = {"id": qid, "category": "animals", "difficulty": ["easy", "medium", "hard"][n % 3],
            "question": "Which animal can sleep standing up?", "options": ["Penguin", "Horse", "Rabbit"],
            "answer_index": 1, "answer": "Horse",
            "fun_fact": "Horses lock their legs so they can doze without falling.",
            "source_url": "https://example.org/horse", "visual_query": "horse sleeping field",
            "verified": True, "flags": []}
    if fmt == "tf":
        base.update(question="Octopuses have three hearts.", options=["True", "False"], answer_index=0, answer="True")
    elif fmt == "riddle":
        base.update(question="What has keys but cannot open a single lock?", options=[], answer_index=-1, answer="A piano")
    elif fmt == "wyr":
        base.update(question="Would you rather...", options=["Fly like a bird", "Breathe underwater"],
                    answer_index=-1, answer="", fun_fact="", source_url="", verified=True)
    return base


def short(i, fmt):
    kind = {"true_false": "tf", "riddle": "riddle", "would_you_rather": "wyr"}.get(fmt, "mcq")
    return {"id": f"s_2026w44_{i:02d}", "format": fmt, "hook": "Only 3% of adults get all three right!",
            "questions": [q(f"q_2026w44_{i:02d}_{n}", kind, n) for n in range(FORMAT_SIZES[fmt])],
            "titles": ["Only 3% of Parents Get All 3 Right", "Your Kid Knows This. Do You?", "Family Brain Clash Challenge"],
            "chosen_title_index": 0, "description": "Can you beat your kids? Comment your score!",
            "first_comment": "Drop your score below 👇 How many did you get?", "hook_id": "h01"}


def make_batch():
    fmts = list(FORMAT_SIZES)
    shorts = [short(i, fmts[i % 6]) for i in range(14)]
    rounds = [{"name": name, "questions": [q(f"l_2026w44_r{r}_{n}", n=n) for n in range(6)]}
              for r, name in enumerate(["Animals", "Geography", "Science", "Food", "Riddles"], 1)]
    long = {"id": "l_2026w44", "episode": 1, "rounds": rounds, "bonus": q("l_2026w44_bonus", n=2),
            "titles": ["Family Quiz Night #1", "30 Questions: Can You Beat Your Kids?", "Family Quiz Night"],
            "chosen_title_index": 0, "description": "Grab a pen and keep score!", "first_comment": "What was your score?",
            "chapters": []}
    return {"week": "2026-W44", "shorts": shorts, "long": long, "report_md": ""}


@pytest.fixture
def batch_dict():
    return copy.deepcopy(make_batch())


@pytest.fixture(autouse=True)
def _page_token_offline(monkeypatch, request):
    """Unit tests never call Graph to resolve the Page token; the env token stands in for it."""
    if "real_page_token" in request.keywords:
        return
    import os
    from fbc.publish import meta
    monkeypatch.setattr(meta, "page_token", lambda: os.environ.get("FB_PAGE_TOKEN", "test-token"))
