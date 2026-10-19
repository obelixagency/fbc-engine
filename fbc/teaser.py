"""Vertical teaser Reel for the weekly long episode: 3 sample questions, then a call to watch the full quiz
on YouTube. Facebook and Instagram get this teaser (with the YouTube link) instead of the full video."""
from __future__ import annotations

from .models import LongItem, ShortItem


def teaser_item(long: LongItem) -> ShortItem:
    picks = []
    for r in (long.rounds[0], long.rounds[2], long.rounds[4]):
        qs = r["questions"]
        picks.append(next((q for q in qs if q.difficulty in ("easy", "medium")), qs[0]))
    return ShortItem(id=f"{long.id}_teaser", format="teaser",
                     hook=f"Family Quiz Night #{long.episode} is live! Can you get these 3?",
                     questions=picks, titles=[long.title], description=long.description,
                     first_comment=long.first_comment)
