import time
from datetime import datetime, timezone

import pytest

from custom_components.esb_smart_meter import api
from custom_components.esb_smart_meter.processing import hourly_totals
from custom_components.esb_smart_meter.api import (
    ESBAuthError,
    ESBCaptchaError,
    ESBLoginBackoff,
    ESBNetworksAPI,
    parse_csv,
)

from .conftest import make_csv

UTC = timezone.utc


def test_timestamps_are_utc_interval_starts():
    data = parse_csv(make_csv([(datetime(2026, 1, 10, 0, 30), 0.25)]))
    assert data.imports == [{"start": datetime(2026, 1, 10, 0, 30, tzinfo=UTC), "kwh": 0.25}]


def test_summer_timestamps_are_not_shifted_to_irish_time():
    data = parse_csv(make_csv([(datetime(2026, 7, 10, 0, 30), 0.25)]))
    assert data.imports[0]["start"] == datetime(2026, 7, 10, 0, 30, tzinfo=UTC)


def test_real_immersion_spike_lands_in_the_hour_it_ran():
    # From a real ESB export, 5 Oct 2026 (Irish summer time): the tank heated
    # 06:00-06:40 UTC and HA's statistics show the whole ~2.1 kWh in 06:00-07:00 UTC.
    rows = [
        (datetime(2026, 10, 5, 5, 30), 0.065),
        (datetime(2026, 10, 5, 6, 0), 1.65),
        (datetime(2026, 10, 5, 6, 30), 0.597),
        (datetime(2026, 10, 5, 7, 0), 0.06),
    ]
    hourly = dict(hourly_totals(parse_csv(make_csv(rows)).imports))
    assert round(hourly[datetime(2026, 10, 5, 6, tzinfo=UTC)], 3) == 2.247
    assert hourly[datetime(2026, 10, 5, 7, tzinfo=UTC)] == 0.06


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
