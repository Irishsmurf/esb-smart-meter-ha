from datetime import datetime, timedelta

import pytest

MPRN = "10012345678"
HEADER = "MPRN,Meter Serial Number,Read Value,Read Type,Read Date and End Time"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("custom_components.esb_smart_meter.api._sleep", lambda *a, **k: None)


def make_csv(rows, descending=True):
    """rows: iterable of (naive ESB timestamp, kWh[, read type])."""
    lines = []
    for row in rows:
        end, kwh, kind = (*row, "Active Import Interval (kWh)")[:3]
        lines.append(f"{MPRN},123,{kwh},{kind},{end:%d-%m-%Y %H:%M}")
    if descending:
        lines.reverse()
    return "\n".join([HEADER, *lines]) + "\n"


def day_rows(start: datetime, days: int, kwh: float = 0.5, kind: str = "Active Import Interval (kWh)"):
    """Half-hourly rows covering whole days from start (ESB stamps 00:00 .. 23:30)."""
    stamp = start
    stop = start + timedelta(days=days)
    while stamp < stop:
        yield stamp, kwh, kind
        stamp += timedelta(minutes=30)
