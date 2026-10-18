"""Copy guard: keeps titles/hooks/descriptions family-directed (spec §3.2).
Phrases that read as kid-directed would push YouTube toward 'Made for Kids'."""
import re

BANNED = ["for kids", "kids quiz", "kids' quiz", "kid's quiz", "toddler", "preschool", "nursery"]


def check_copy(text: str) -> list[str]:
    low = re.sub(r"\s+", " ", (text or "").lower())
    return [f'contains banned phrase "{p}"' for p in BANNED if p in low]
