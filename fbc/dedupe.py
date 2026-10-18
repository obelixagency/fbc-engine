"""Question de-duplication against the used bank (spec §3.3: no repeat within 180 days)."""
import hashlib
import re
from datetime import date, timedelta

STOPWORDS = {"a", "an", "the", "which", "what", "who", "is", "are", "was", "able", "to", "can",
             "does", "do", "of", "in", "on", "for", "and", "or", "how", "many", "much", "this", "that"}
JACCARD = 0.8


def normalize(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def fingerprint(question: str, answer: str) -> str:
    return hashlib.sha1(f"{normalize(question)}|{normalize(answer)}".encode()).hexdigest()


def _content_words(text: str) -> set[str]:
    return {w for w in normalize(text).split() if w not in STOPWORDS}


def is_duplicate(question: str, answer: str, used: list[dict], days: int = 180, today: date | None = None) -> bool:
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    fp = fingerprint(question, answer)
    words = _content_words(question)
    ans = normalize(answer)
    for u in used:
        if date.fromisoformat(u["date"][:10]) < cutoff:
            continue
        if u.get("fingerprint") == fp:
            return True
        if normalize(u.get("answer", "")) != ans:
            continue
        other = _content_words(u.get("question", ""))
        union = words | other
        if union and len(words & other) / len(union) >= JACCARD:
            return True
    return False
