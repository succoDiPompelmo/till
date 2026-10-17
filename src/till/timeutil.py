"""Time helpers. Timestamps are stored in UTC as 'YYYY-MM-DD HH:MM:SS'."""

from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DB_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_zone(name: str | None) -> tzinfo:
    try:
        return ZoneInfo(name) if name else UTC
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def to_db(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime(DB_FORMAT)


def from_db(value: str, zone: tzinfo) -> datetime:
    return datetime.strptime(value, DB_FORMAT).replace(tzinfo=UTC).astimezone(zone)


def parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt


def period_bounds(period: str, now: datetime, zone: tzinfo) -> tuple[datetime, datetime, str]:
    """Return (start, end, label) in UTC for 'today', 'week', 'month', 'year' or 'YYYY-MM'."""
    local = now.astimezone(zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        start, end, label = midnight, midnight + timedelta(days=1), f"Today ({local:%a %d %b})"
    elif period == "week":
        start = midnight - timedelta(days=local.weekday())
        end, label = start + timedelta(days=7), f"Week of {start:%d %b}"
    elif period == "year":
        start = midnight.replace(month=1, day=1)
        end, label = start.replace(year=start.year + 1), f"{start:%Y}"
    else:
        if period == "month":
            start = midnight.replace(day=1)
        else:
            try:
                parsed = datetime.strptime(period, "%Y-%m")
            except ValueError as e:
                raise ValueError(f"Unknown period {period!r}; use today, week, month, year or YYYY-MM") from e
            start = midnight.replace(year=parsed.year, month=parsed.month, day=1)
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        label = f"{start:%B %Y}"
    # Re-localize to get the right UTC offset across DST changes.
    start = start.replace(tzinfo=None).replace(tzinfo=zone)
    end = end.replace(tzinfo=None).replace(tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC), label
