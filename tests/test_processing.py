from datetime import date, datetime, timedelta, timezone

from custom_components.esb_smart_meter.api import parse_csv
from custom_components.esb_smart_meter.processing import hourly_totals, last_complete_day, latest_reading_end

from .conftest import day_rows, make_csv

UTC = timezone.utc


def test_hourly_totals_group_half_hours():
    data = parse_csv(make_csv(day_rows(datetime(2026, 1, 10), 1)))
    hourly = hourly_totals(data.imports)
    assert len(hourly) == 24
    assert hourly[0] == (datetime(2026, 1, 10, 0, tzinfo=UTC), 1.0)


def test_last_complete_day_in_summer_uses_irish_days():
    # Two full Irish days of data, plus 3 hours of a third.
    rows = list(day_rows(datetime(2026, 7, 1), 2, kwh=0.5))
    rows += [(datetime(2026, 7, 3, 0, 30) + timedelta(minutes=30 * i), 2.0) for i in range(6)]
    data = parse_csv(make_csv(rows))
    assert latest_reading_end(data.imports) == datetime(2026, 7, 3, 2, 0, tzinfo=UTC)  # 03:00 IST
    assert last_complete_day(data.imports) == (date(2026, 7, 2), 24.0)


def test_last_complete_day_when_data_ends_at_midnight():
    data = parse_csv(make_csv(day_rows(datetime(2026, 1, 1), 3, kwh=0.25)))
    assert last_complete_day(data.imports) == (date(2026, 1, 3), 12.0)


def test_no_data():
    assert last_complete_day([]) is None
    assert latest_reading_end([]) is None
