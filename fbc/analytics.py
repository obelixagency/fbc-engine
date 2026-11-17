"""Analytics loop (spec §6.5): score each video 72 h after publishing and re-weight
formats, categories, hooks and slots for the next batch."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import requests

from .log import safe

from .config import env
from .weights import score_video, update_weights

YT_ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
GRAPH = "https://graph.facebook.com/v26.0"
MIN_AGE = timedelta(hours=72)



def _meta_token() -> str:
    from .publish.meta import page_token
    return page_token()

def fetch_youtube(video_id: str, start: date, token: str | None = None) -> dict | None:
    from .publish.youtube import access_token
    token = token or access_token()
    r = requests.get(YT_ANALYTICS, headers={"Authorization": f"Bearer {token}"}, timeout=60, params={
        "ids": "channel==MINE", "startDate": start.isoformat(), "endDate": date.today().isoformat(),
        "metrics": "views,averageViewPercentage,likes,comments,shares,subscribersGained",
        "filters": f"video=={video_id}"})
    data = r.json()
    if r.status_code >= 400 or not data.get("rows"):
        return None
    cols = [c["name"] for c in data["columnHeaders"]]
    return dict(zip(cols, data["rows"][0]))


def fetch_instagram(media_id: str, duration: float) -> dict | None:
    r = requests.get(f"{GRAPH}/{media_id}/insights", timeout=60, params={
        "metric": "views,likes,comments,shares,ig_reels_avg_watch_time", "access_token": _meta_token()})
    data = r.json()
    if r.status_code >= 400 or "data" not in data:
        err = data.get("error") if isinstance(data, dict) else None
        safe("ig_insights_failed", http=r.status_code,
             reason=f"code_{err.get('code')}" if isinstance(err, dict) else None)   # usually a missing permission
        return None
    m = {d["name"]: (d.get("values") or [{}])[0].get("value", 0) for d in data["data"]}
    views = m.get("views") or 0
    if not views:
        return None
    avg_pct = min(100.0, (m.get("ig_reels_avg_watch_time", 0) / 1000.0) / max(duration, 1) * 100)
    return {"views": views, "averageViewPercentage": avg_pct, "likes": m.get("likes", 0),
            "comments": m.get("comments", 0), "shares": m.get("shares", 0), "subscribersGained": 0}


def fetch_facebook(video_id: str, duration: float) -> dict | None:
    """Reel plays and average watch time (video_insights; needs read_insights)."""
    token = _meta_token()
    r = requests.get(f"{GRAPH}/{video_id}/video_insights", timeout=60, params={
        "metric": "blue_reels_play_count,post_video_avg_time_watched", "access_token": token})
    data = r.json()
    if r.status_code >= 400 or "data" not in data:
        err = data.get("error") if isinstance(data, dict) else None
        safe("fb_insights_failed", http=r.status_code,
             reason=f"code_{err.get('code')}" if isinstance(err, dict) else None)
        return None
    m = {d["name"]: (d.get("values") or [{}])[0].get("value", 0) for d in data["data"]}
    views = m.get("blue_reels_play_count") or 0
    if not views:
        return None
    avg_pct = min(100.0, (m.get("post_video_avg_time_watched", 0) / 1000.0) / max(duration, 1) * 100)
    eng = requests.get(f"{GRAPH}/{video_id}", timeout=60, params={
        "fields": "likes.summary(true).limit(0),comments.summary(true).limit(0)", "access_token": token}).json()
    def total(k):
        return ((eng.get(k) or {}).get("summary") or {}).get("total_count", 0) if isinstance(eng, dict) else 0
    return {"views": views, "averageViewPercentage": avg_pct, "likes": total("likes"),
            "comments": total("comments"), "shares": 0, "subscribersGained": 0}


def _slot_et(entry: dict) -> str | None:
    from zoneinfo import ZoneInfo
    from .config import TZ_ET
    try:
        return datetime.fromisoformat(entry["slot_utc"]).astimezone(ZoneInfo(TZ_ET)).strftime("%H:%M")
    except (KeyError, ValueError):
        return None


def _row(entry: dict, platform: str, s: dict) -> dict:
    views = max(s.get("views") or 0, 1)
    score = score_video(s.get("averageViewPercentage", 0) / 100, s.get("shares", 0) / views,
                        s.get("comments", 0) / views, s.get("subscribersGained", 0) / views)
    return {"id": entry["id"], "platform": platform, "format": entry.get("format"), "category": entry.get("category"),
            "hook_id": entry.get("hook_id"), "slot": entry.get("slot_local") or _slot_et(entry), "views": s.get("views", 0),
            "avg_view_pct": s.get("averageViewPercentage", 0), "score": round(score, 5)}


MAX_AGE = timedelta(days=60)   # stop retrying a platform that never returns stats


def collect_stats(queue: list[dict], now: datetime, fetch_yt=fetch_youtube, fetch_ig=fetch_instagram,
                  fetch_fb=fetch_facebook) -> list[dict]:
    """Scores each platform once per Short. `scored_on` lists the platforms already scored; old entries that only
    have `scored` were scored on YouTube alone, so Instagram and Facebook are still collected for them."""
    rows = []
    fetchers = {"youtube": lambda ref, e, pub: fetch_yt(ref, pub.date()),
                "instagram": lambda ref, e, pub: fetch_ig(ref, e.get("duration", 30.0)),
                "facebook": lambda ref, e, pub: fetch_fb(ref, e.get("duration", 30.0))}
    for e in queue:
        if e.get("kind") != "short" or e.get("status") != "published" or not e.get("published_at"):
            continue
        published = datetime.fromisoformat(e["published_at"])
        if now - published < MIN_AGE or now - published > MAX_AGE:
            continue
        done = set(e.get("scored_on") or (["youtube"] if e.get("scored") else []))
        for platform, fetch in fetchers.items():
            ref = (e["platforms"].get(platform) or {}).get("ref")
            if platform in done or not ref:
                continue
            s = fetch(ref, e, published)
            if s:
                rows.append(_row(e, platform, s)); done.add(platform)
        if done:
            e["scored_on"] = sorted(done)
            e["scored"] = True
    return rows


def reach_md(scores: list[dict]) -> str:
    """Median views per platform and ET slot, from analytics/scores.jsonl rows."""
    from statistics import median
    by: dict[tuple, list] = {}
    for r in scores:
        by.setdefault((r.get("platform"), r.get("slot") or "?"), []).append(r.get("views") or 0)
    out = ["| platform | slot ET | Shorts | median views | total views |", "|---|---|---|---|---|"]
    for (p, slot), v in sorted(by.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
        out.append(f"| {p} | {slot} | {len(v)} | {median(v):.0f} | {sum(v)} |")
    return "\n".join(out)


def apply(weights: dict, rows: list[dict]) -> dict:
    per_item: dict[str, dict] = {}
    for r in rows:
        agg = per_item.setdefault(r["id"], {**r, "scores": []})
        agg["scores"].append(r["score"])
    scored = [{**v, "score": sum(v["scores"]) / len(v["scores"])} for v in per_item.values()]
    return update_weights(weights, scored)


def report_md(rows: list[dict], before: dict, after: dict) -> str:
    if not rows:
        return "_No videos old enough to score yet._"
    best = sorted(rows, key=lambda r: r["score"], reverse=True)
    def line(r):
        return f"| {r['id']} | {r['platform']} | {r['format']} | {r['views']} | {r['avg_view_pct']:.0f}% | {r['score']:.3f} |"
    head = "| video | platform | format | views | avg viewed | score |\n|---|---|---|---|---|---|"
    changes = "\n".join(f"- {k}: {before['formats'].get(k, 0):.2f} → {after['formats'][k]:.2f}" for k in after["formats"])
    return (f"### Top 3\n{head}\n" + "\n".join(map(line, best[:3])) +
            f"\n\n### Bottom 3\n{head}\n" + "\n".join(map(line, best[-3:])) + f"\n\n### Format weights\n{changes}\n")
