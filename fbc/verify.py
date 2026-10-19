"""Second, independent pass: grounded fact-check of every answer (spec §5)."""
import json
from pathlib import Path

from .gemini import QuotaExceeded, generate_json
from .models import Question

PROMPT = (Path(__file__).resolve().parent.parent / "prompts" / "verify.md").read_text()
CHUNK = 25   # fewer grounded calls: the free tier allows only ~20 text requests/day


def verify_questions(qs: list[Question], gen=generate_json) -> list[Question]:
    for i in range(0, len(qs), CHUNK):
        chunk = qs[i:i + CHUNK]
        items = "\n".join(json.dumps({"id": q.id, "question": q.question, "options": q.options,
                                      "stated_answer": q.answer, "fun_fact": q.fun_fact}, ensure_ascii=False)
                          for q in chunk)
        try:
            verdicts = {v.get("id"): v for v in gen(PROMPT.format(items=items), {}, grounded=True) or []}
        except QuotaExceeded:
            # out of search-grounding quota: keep the batch, flag the rest for a human fact-check in the PR
            for q in qs[i:]:
                q.verified = False
                q.flags.append("⚠️ unverified: fact-check quota reached, please check this answer by hand")
            break
        for q in chunk:
            v = verdicts.get(q.id)
            if v and v.get("correct") and v.get("source_url"):
                q.verified, q.source_url = True, v["source_url"]
            else:
                q.verified = False
                note = (v or {}).get("note") or "no verdict or no source"
                q.flags.append(f"⚠️ unverified: {note}")
                if v and v.get("source_url"):
                    q.source_url = v["source_url"]
    return qs
