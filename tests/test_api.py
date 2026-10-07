import time
from datetime import datetime, timezone

import pytest

from custom_components.esb_smart_meter import api
from custom_components.esb_smart_meter.api import (
    ESBAuthError,
    ESBCaptchaError,
    ESBLoginBackoff,
    ESBNetworksAPI,
    parse_csv,
)

from .conftest import make_csv

UTC = timezone.utc


def test_winter_readings_are_interval_start_in_utc():
    data = parse_csv(make_csv([(datetime(2026, 1, 10, 0, 30), 0.25)]))
    assert data.imports == [{"start": datetime(2026, 1, 10, 0, 0, tzinfo=UTC), "kwh": 0.25}]


def test_summer_readings_account_for_irish_summer_time():
    # 00:30 IST end -> 00:00 IST start -> 23:00 UTC the previous day.
    data = parse_csv(make_csv([(datetime(2026, 7, 10, 0, 30), 0.25)]))
    assert data.imports[0]["start"] == datetime(2026, 7, 9, 23, 0, tzinfo=UTC)


@pytest.mark.parametrize("descending", [True, False])
def test_clocks_going_back_keeps_both_repeated_half_hours(descending):
    # 25 Oct 2026: 02:00 IST -> 01:00 GMT, so 01:00 and 01:30 appear twice.
    ends = ["00:30", "01:00", "01:30", "01:00", "01:30", "02:00"]
    rows = [(datetime.strptime(f"25-10-2026 {t}", "%d-%m-%Y %H:%M"), i) for i, t in enumerate(ends)]
    data = parse_csv(make_csv(rows, descending=descending))
    starts = [r["start"] for r in data.imports]
    assert starts == [
        datetime(2026, 10, 24, 23, 0, tzinfo=UTC),
        datetime(2026, 10, 24, 23, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 0, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 30, tzinfo=UTC),
    ]
    assert [r["kwh"] for r in data.imports] == [0, 1, 2, 3, 4, 5]


def test_export_rows_are_separated():
    data = parse_csv(make_csv([
        (datetime(2026, 1, 10, 0, 30), 0.25),
        (datetime(2026, 1, 10, 0, 30), 0.1, "Active Export Interval (kWh)"),
        (datetime(2026, 1, 10, 0, 30), 9, "Reactive Import Interval (kvarh)"),
    ]))
    assert [r["kwh"] for r in data.imports] == [0.25]
    assert [r["kwh"] for r in data.exports] == [0.1]


def _api(tmp_path, password="pw"):
    return ESBNetworksAPI("me@example.com", password, "100 1234 5678", str(tmp_path / "cache.json"))


def test_failed_login_backs_off_for_same_credentials(tmp_path, monkeypatch):
    calls = []

    def fake_login(self):
        calls.append(1)
        raise ESBCaptchaError("captcha")

    monkeypatch.setattr(ESBNetworksAPI, "_login", fake_login)
    with pytest.raises(ESBCaptchaError):
        _api(tmp_path).login()
    with pytest.raises(ESBLoginBackoff):
        _api(tmp_path).login()
    assert len(calls) == 1

    # New credentials (e.g. after re-auth) are not held back.
    with pytest.raises(ESBCaptchaError):
        _api(tmp_path, password="new").login()
    assert len(calls) == 2


def test_network_errors_do_not_back_off(tmp_path, monkeypatch):
    calls = []

    def fake_login(self):
        calls.append(1)
        raise api.ESBError("step 1 failed: connection reset")

    monkeypatch.setattr(ESBNetworksAPI, "_login", fake_login)
    for _ in range(2):
        with pytest.raises(api.ESBError):
            _api(tmp_path).login()
    assert len(calls) == 2


def test_backoff_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(ESBNetworksAPI, "_login", lambda self: (_ for _ in ()).throw(ESBAuthError("no")))
    with pytest.raises(ESBAuthError):
        _api(tmp_path).login()
    later = time.time() + api.LOGIN_BACKOFF.total_seconds() + 1
    monkeypatch.setattr(api.time, "time", lambda: later)
    with pytest.raises(ESBAuthError):
        _api(tmp_path).login()


class _FakeResponse:
    content = b"x" * 200
    text = "csv" * 70
    headers = {}

    def raise_for_status(self):
        pass


class _FakeSession:
    def post(self, *args, **kwargs):
        return _FakeResponse()


def test_cached_session_is_reused_without_age_limit(tmp_path, monkeypatch):
    client = _api(tmp_path)
    client._write_cache({"cookies": {"a": "b"}, "saved_at": 0})  # saved in 1970
    monkeypatch.setattr(
        ESBNetworksAPI, "_validate_session", lambda self, c: setattr(self, "_session", _FakeSession()) or True
    )
    monkeypatch.setattr(ESBNetworksAPI, "_login", lambda self: pytest.fail("should not log in"))
    monkeypatch.setattr(ESBNetworksAPI, "_get_xsrf_token", lambda self: "token")
    assert client.download_csv() == _FakeResponse.text
