from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fbc import schedule

ET = ZoneInfo("America/New_York")


def test_week_slots_across_dst():
    slots = schedule.week_slots("2026-W44")   # Mon Oct 26 .. Sun Nov 1 2026 (DST ends Nov 1)
    assert len(slots) == 29
    shorts = [t for k, t in slots if k == "short"]
    longs = [t for k, t in slots if k == "long"]
    assert len(shorts) == 28 and len(longs) == 1
    local = [t.astimezone(ET) for t in shorts]
    assert {(t.hour, t.minute) for t in local} == {(7, 0), (12, 0), (16, 0), (20, 0)}
    assert len(set(shorts)) == 28
    assert local[-1].date().isoformat() == "2026-11-01"
    assert longs[0].astimezone(ET).strftime("%a %H:%M") == "Sat 10:00"
    assert local[-1].utcoffset() != local[0].utcoffset()   # DST really changed inside the week


def test_due_and_late():
    now = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
    q = [
        {"id": "a", "status": "produced", "slot_utc": (now - timedelta(minutes=5)).isoformat(), "platforms": {}},
        {"id": "b", "status": "produced", "slot_utc": (now + timedelta(hours=1)).isoformat(), "platforms": {}},
        {"id": "c", "status": "planned", "slot_utc": (now - timedelta(hours=3)).isoformat(), "platforms": {}},
        {"id": "d", "status": "planned", "slot_utc": (now - timedelta(hours=2)).isoformat(), "platforms": {}},
        {"id": "e", "status": "published", "slot_utc": (now - timedelta(hours=9)).isoformat(), "platforms": {}},
    ]
    assert [x["id"] for x in schedule.due_items(q, now)] == ["a"]
    assert [x["id"] for x in schedule.late_items(q, now)] == ["c"]
    assert [x["id"] for x in schedule.to_produce(q, now, hours=48)] == ["c", "d"]


def test_older_14_short_batch_still_plans_and_loads():
    """W43 was approved with 14 Shorts; a batch with fewer Shorts than slots fills the earliest slots only."""
    from fbc.models import batch_from_obj
    from fbc.store import plan_queue
    from tests.conftest import make_batch
    b = batch_from_obj(make_batch())
    q = plan_queue(b)
    assert len([e for e in q if e["kind"] == "short"]) == 14 and len([e for e in q if e["kind"] == "long"]) == 1
