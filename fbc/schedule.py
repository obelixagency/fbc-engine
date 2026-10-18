"""Publishing slots in America/New_York (spec §6.6) and queue selection helpers."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import LIMITS, LONG_SLOT_ET, SHORT_SLOTS_ET, TZ_ET

ET = ZoneInfo(TZ_ET)
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _at(day: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(day, time(h, m), tzinfo=ET).astimezone(timezone.utc)


def week_monday(week: str) -> date:
    year, w = week.split("-W")
    return date.fromisocalendar(int(year), int(w), 1)


def next_week(today: date) -> str:
    y, w, _ = (today + timedelta(days=7)).isocalendar()
    return f"{y}-W{w:02d}"


def week_slots(week: str) -> list[tuple[str, datetime]]:
    monday = week_monday(week)
    slots = []
    for d in range(7):
        day = monday + timedelta(days=d)
        for hhmm in SHORT_SLOTS_ET:
            slots.append(("short", _at(day, hhmm)))
        if DAYS[d] == LONG_SLOT_ET[0]:
            slots.append(("long", _at(day, LONG_SLOT_ET[1])))
    return sorted(slots, key=lambda x: x[1])


def _slot(e) -> datetime:
    return datetime.fromisoformat(e["slot_utc"])


def due_items(queue: list[dict], now: datetime) -> list[dict]:
    return [e for e in queue if e["status"] == "produced" and _slot(e) <= now]


def late_items(queue: list[dict], now: datetime, grace_h: float = LIMITS["late_grace_h"], window_h: float = 24) -> list[dict]:
    """Unpublished entries 2.5 h past their slot, only within a day (so an Issue is opened once, not forever)."""
    return [e for e in queue if e["status"] not in ("published", "skipped")
            and _slot(e) + timedelta(hours=grace_h) < now <= _slot(e) + timedelta(hours=window_h)]


def to_produce(queue: list[dict], now: datetime, hours: float = 48) -> list[dict]:
    return sorted([e for e in queue if e["status"] == "planned" and _slot(e) <= now + timedelta(hours=hours)], key=_slot)
