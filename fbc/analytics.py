"""Analytics loop (spec §6.5): score each video 72 h after publishing and re-weight
formats, categories, hooks and slots for the next batch."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import requests

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
        return None
    m = {d["name"]: (d.get("values") or [{}])[0].get("value", 0) for d in data["data"]}
    views = m.get("views") or 0
    if not views:
        return None
    avg_pct = min(100.0, (m.get("ig_reels_avg_watch_time", 0) / 1000.0) / max(duration, 1) * 100)
    return {"views": views, "averageViewPercentage": avg_pct, "likes": m.get("likes", 0),
            "comments": m.get("comments", 0), "shares": m.get("shares", 0), "subscribersGained": 0}


def _row(entry: dict, platform: str, s: dict) -> dict:
    views = max(s.get("views") or 0, 1)
    score = score_video(s.get("averageViewPercentage", 0) / 100, s.get("shares", 0) / views,
                        s.get("comments", 0) / views, s.get("subscribersGained", 0) / views)
    return {"id": entry["id"], "platform": platform, "format": entry.get("format"), "category": entry.get("category"),
            "hook_id": entry.get("hook_id"), "slot": entry.get("slot_local"), "views": s.get("views", 0),
            "avg_view_pct": s.get("averageViewPercentage", 0), "score": round(score, 5)}


def collect_stats(queue: list[dict], now: datetime, fetch_yt=fetch_youtube, fetch_ig=fetch_instagram) -> list[dict]:
    rows = []
    for e in queue:
        if e.get("kind") != "short" or e.get("status") != "published" or e.get("scored") or not e.get("published_at"):
            continue
        published = datetime.fromisoformat(e["published_at"])
        if now - published < MIN_AGE:
            continue
        got = False
        yt_ref = e["platforms"].get("youtube", {}).get("ref")
        if yt_ref:
            s = fetch_yt(yt_ref, published.date())
            if s:
                rows.append(_row(e, "youtube", s)); got = True
        ig_ref = e["platforms"].get("instagram", {}).get("ref")
        if ig_ref:
            s = fetch_ig(ig_ref, e.get("duration", 30.0))
            if s:
                rows.append(_row(e, "instagram", s)); got = True
        if got:
            e["scored"] = True
    return rows


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
