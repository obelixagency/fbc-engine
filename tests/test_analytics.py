from datetime import datetime, timedelta, timezone

from fbc import analytics
from fbc.weights import default_weights

NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)


def e(i, fmt, hours_ago, ig=True):
    return {"id": i, "kind": "short", "status": "published", "format": fmt, "category": "animals", "hook_id": "h01",
            "duration": 25.0, "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "platforms": {"youtube": {"status": "published", "ref": f"yt_{i}"},
                          "instagram": {"status": "published", "ref": f"ig_{i}" if ig else None},
                          "facebook": {"status": "published", "ref": f"fb_{i}"}}}


def yt(ref, day):
    good = "zoom" in ref
    return {"views": 1000, "averageViewPercentage": 90 if good else 40, "likes": 50, "comments": 30 if good else 2,
            "shares": 20 if good else 1, "subscribersGained": 5 if good else 0}


def ig_missing(ref, duration):
    return None


def test_skips_items_under_72h():
    rows = analytics.collect_stats([e("zoom1", "zoom_reveal", 71)], NOW, yt, ig_missing, ig_missing)
    assert rows == []


def test_scores_and_updates_weights():
    q = [e("zoom1", "zoom_reveal", 80), e("riddle1", "riddle", 80)]
    rows = analytics.collect_stats(q, NOW, yt, ig_missing, ig_missing)
    assert {r["id"] for r in rows} == {"zoom1", "riddle1"}
    assert all(r["platform"] == "youtube" for r in rows)   # Instagram metrics missing -> omitted
    w = analytics.apply(default_weights(), rows)
    assert w["formats"]["zoom_reveal"] > w["formats"]["riddle"]
    assert all(x.get("scored") for x in q)


def meta_ok(ref, duration):
    return {"views": 300 if ref.startswith("ig") else 120, "averageViewPercentage": 60, "likes": 3, "comments": 1,
            "shares": 0, "subscribersGained": 0}


def test_backfills_instagram_and_facebook_for_shorts_scored_on_youtube_only():
    old = e("zoom1", "zoom_reveal", 200); old["scored"] = True; old["slot_utc"] = "2026-11-02T21:00:00+00:00"
    rows = analytics.collect_stats([old], NOW, yt, meta_ok, meta_ok)
    assert sorted(r["platform"] for r in rows) == ["facebook", "instagram"]
    assert old["scored_on"] == ["facebook", "instagram", "youtube"]
    assert rows[0]["slot"] == "16:00"          # ET slot derived from slot_utc for entries without slot_local
    assert analytics.collect_stats([old], NOW, yt, meta_ok, meta_ok) == []   # each platform scored once


def test_platform_without_stats_is_retried_next_time():
    q = [e("riddle1", "riddle", 80)]
    assert [r["platform"] for r in analytics.collect_stats(q, NOW, yt, ig_missing, ig_missing)] == ["youtube"]
    assert sorted(r["platform"] for r in analytics.collect_stats(q, NOW, yt, meta_ok, meta_ok)) == ["facebook", "instagram"]


def test_reach_md_medians_per_platform_and_slot():
    md = analytics.reach_md([{"platform": "youtube", "slot": "16:00", "views": v} for v in (100, 200, 300)])
    assert "| youtube | 16:00 | 3 | 200 | 600 |" in md
