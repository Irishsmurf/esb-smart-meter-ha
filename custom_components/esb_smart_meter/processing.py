"""Pure helpers for turning parsed ESB readings into totals (no Home Assistant imports)."""

from datetime import date, datetime, timedelta, timezone

from .api import ESB_TZ, INTERVAL


def hourly_totals(readings: list[dict]) -> list[tuple[datetime, float]]:
    """Sum half-hourly readings into UTC hour buckets, oldest first."""
    hourly: dict[datetime, float] = {}
    for reading in readings:
        bucket = reading["start"].astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hourly[bucket] = hourly.get(bucket, 0.0) + reading["kwh"]
    return sorted(hourly.items())


def latest_reading_end(readings: list[dict]) -> datetime | None:
    """When the newest interval in the data finished (UTC)."""
    return readings[-1]["start"] + INTERVAL if readings else None


def last_complete_day(readings: list[dict]) -> tuple[date, float] | None:
    """The most recent calendar day (in ESB's timestamps) fully covered by the data, and its total kWh.

    The day containing the newest reading is only complete if that reading ended
    exactly at midnight, so the answer is always the day before the newest
    reading's end date.
    """
    end = latest_reading_end(readings)
    if end is None:
        return None
    day = end.astimezone(ESB_TZ).date() - timedelta(days=1)
    values = [r["kwh"] for r in readings if r["start"].astimezone(ESB_TZ).date() == day]
    if not values:
        return None
    return day, round(sum(values), 3)
