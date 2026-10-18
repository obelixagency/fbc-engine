"""Self-tuning weights for formats, categories, hooks and slots (spec §6.5)."""
import random

FORMATS = ["three_q", "zoom_reveal", "riddle", "parents_vs_kids", "true_false", "would_you_rather"]
CATEGORIES = ["animals", "geography", "science", "food", "space", "human_body", "history", "sports",
              "riddles", "words"]
FLOOR = 0.05
EXPLORE = 0.2


def _even(keys) -> dict:
    return {k: 1 / len(keys) for k in keys}


def default_weights() -> dict:
    return {"formats": _even(FORMATS), "categories": _even(CATEGORIES), "hooks": {}, "slots": {}}


def score_video(avg_view_pct: float, shares_rate: float, comments_rate: float, subs_rate: float) -> float:
    return 0.5 * avg_view_pct + 0.2 * shares_rate + 0.2 * comments_rate + 0.1 * subs_rate


def _normalize(group: dict) -> dict:
    if not group:
        return group
    total = sum(group.values())
    out = {k: v / total for k, v in group.items()}
    for _ in range(10):  # clamp to the floor, then redistribute among the rest
        low = {k for k, v in out.items() if v < FLOOR}
        if not low:
            break
        rest = sum(v for k, v in out.items() if k not in low)
        budget = 1 - FLOOR * len(low)
        out = {k: FLOOR if k in low else v / rest * budget for k, v in out.items()}
    return out


def update_weights(w: dict, scored: list[dict]) -> dict:
    """Each key's weight is multiplied by 0.7 + 0.3 * (its mean score / overall mean score)."""
    if not scored:
        return w
    overall = sum(s["score"] for s in scored) / len(scored) or 1e-9
    new = {g: dict(v) for g, v in w.items()}
    for group, field in (("formats", "format"), ("categories", "category"), ("hooks", "hook_id"), ("slots", "slot")):
        by_key: dict[str, list[float]] = {}
        for s in scored:
            if s.get(field):
                by_key.setdefault(s[field], []).append(s["score"])
        for key, vals in by_key.items():
            ratio = (sum(vals) / len(vals)) / overall
            base = new[group].get(key, 1 / max(len(new[group]), 1))
            new[group][key] = base * (0.7 + 0.3 * ratio)
        new[group] = _normalize(new[group])
    return new


def _weighted(group: dict, rng: random.Random) -> str:
    keys = list(group)
    return rng.choices(keys, weights=[group[k] for k in keys])[0]


def pick_formats(w: dict, n: int, rng: random.Random, return_explore: bool = False):
    explore = round(EXPLORE * n)
    picks = list(FORMATS) if n >= len(FORMATS) else []
    while len(picks) < n - explore:
        picks.append(_weighted(w["formats"], rng))
    picks += [rng.choice(FORMATS) for _ in range(n - len(picks))]
    rng.shuffle(picks)
    return (picks, explore) if return_explore else picks


def pick_category(w: dict, rng: random.Random) -> str:
    return _weighted(w["categories"], rng)
