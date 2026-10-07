from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics, statistics_during_period
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.esb_smart_meter.api import ESBAuthError, ESBCaptchaError
from custom_components.esb_smart_meter.const import DOMAIN

from .conftest import MPRN, day_rows, make_csv

UTC = timezone.utc
STAT_ID = f"{DOMAIN}:consumption_{MPRN}"
EXPORT_ID = f"{DOMAIN}:export_{MPRN}"
DOWNLOAD = "custom_components.esb_smart_meter.api.ESBNetworksAPI.download_csv"
EXPORT = "Active Export Interval (kWh)"


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MPRN,
        data={"username": "me@example.com", "password": "pw", "mprn": MPRN},
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass, entry, download):
    with patch(DOWNLOAD, side_effect=download):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)


async def _refresh(hass, entry, download):
    with patch(DOWNLOAD, side_effect=download):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)


async def _stats(hass, statistic_id=STAT_ID):
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2020, 1, 1, tzinfo=UTC), None,
        {statistic_id}, "hour", None, {"sum", "state"},
    )
    return [
        (dt_util.utc_from_timestamp(r["start"]), round(r["state"], 3), round(r["sum"], 3))
        for r in result.get(statistic_id, [])
    ]


def _state(hass, key):
    unique_id = f"{DOMAIN}_{MPRN}" if key == "last_day_consumption" else f"{DOMAIN}_{MPRN}_{key}"
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, unique_id)
    return hass.states.get(entity_id) if entity_id else None


async def test_setup_writes_statistics_in_real_time_and_sensors(recorder_mock, hass: HomeAssistant, entry, enable_custom_integrations):
    # Two full summer days, 1 kWh per half hour.
    csv = make_csv(day_rows(datetime(2026, 7, 1), 2, kwh=1.0))
    await _setup(hass, entry, lambda: csv)

    stats = await _stats(hass)
    assert stats[0] == (datetime(2026, 7, 1, 0, tzinfo=UTC), 2.0, 2.0)
    assert stats[-1] == (datetime(2026, 7, 2, 23, tzinfo=UTC), 2.0, 96.0)
    assert len(stats) == 48

    consumption = _state(hass, "last_day_consumption")
    assert consumption.state == "48.0"
    assert consumption.attributes["date"] == "2026-07-02"
    assert consumption.attributes["statistic_id"] == STAT_ID
    assert "state_class" not in consumption.attributes
    assert consumption.name == "ESB Smart Meter Last complete day consumption"
    assert _state(hass, "latest_reading").state == "2026-07-03T00:00:00+00:00"
    assert _state(hass, "last_success").state not in ("unknown", "unavailable")
    assert _state(hass, "last_day_export") is None  # no export rows


async def test_sum_stays_continuous_when_esb_window_rolls(recorder_mock, hass, entry, enable_custom_integrations):
    await _setup(hass, entry, lambda: make_csv(day_rows(datetime(2026, 1, 1), 3, kwh=1.0)))
    # Next download: the oldest day has dropped off, a new day has arrived.
    await _refresh(hass, entry, lambda: make_csv(day_rows(datetime(2026, 1, 2), 3, kwh=1.0)))

    stats = await _stats(hass)
    assert len(stats) == 96
    sums = [s[2] for s in stats]
    assert sums == [2.0 * (i + 1) for i in range(96)]  # no cliff, still monotonic


async def test_export_statistic_and_sensor(recorder_mock, hass, entry, enable_custom_integrations):
    rows = [*day_rows(datetime(2026, 1, 1), 1, kwh=1.0), *day_rows(datetime(2026, 1, 1), 1, kwh=0.25, kind=EXPORT)]
    await _setup(hass, entry, lambda: make_csv(rows))

    export_stats = await _stats(hass, EXPORT_ID)
    assert export_stats[-1][2] == 12.0
    assert _state(hass, "last_day_export").state == "12.0"
    assert (await _stats(hass))[-1][2] == 48.0


async def test_failure_keeps_data_and_flags_repair_after_a_day(recorder_mock, hass, entry, freezer, enable_custom_integrations):
    await _setup(hass, entry, lambda: make_csv(day_rows(datetime(2026, 1, 1), 1)))

    def blocked():
        raise ESBCaptchaError("captcha")

    await _refresh(hass, entry, blocked)
    assert _state(hass, "last_day_consumption").state == "24.0"
    assert _state(hass, "last_success").attributes["last_error"] == "captcha"
    issues = ir.async_get(hass)
    assert not issues.async_get_issue(DOMAIN, f"update_failed_{entry.entry_id}")

    freezer.tick(timedelta(hours=25))
    await _refresh(hass, entry, blocked)
    assert issues.async_get_issue(DOMAIN, f"update_failed_{entry.entry_id}")
    assert _state(hass, "last_day_consumption").state == "24.0"

    # Recovers and clears the issue.
    await _refresh(hass, entry, lambda: make_csv(day_rows(datetime(2026, 1, 1), 2)))
    assert not issues.async_get_issue(DOMAIN, f"update_failed_{entry.entry_id}")
    assert _state(hass, "last_success").attributes["last_error"] is None


async def test_data_survives_restart_when_fetch_fails(recorder_mock, hass, entry, enable_custom_integrations):
    await _setup(hass, entry, lambda: make_csv(day_rows(datetime(2026, 1, 1), 1)))
    assert await hass.config_entries.async_unload(entry.entry_id)

    def blocked():
        raise ESBCaptchaError("captcha")

    await _setup(hass, entry, blocked)
    state = _state(hass, "last_day_consumption")
    assert state.state == "24.0"
    assert state.attributes["date"] == "2026-01-01"


async def test_rejected_password_starts_reauth(recorder_mock, hass, entry, enable_custom_integrations):
    def rejected():
        raise ESBAuthError("login rejected")

    await _setup(hass, entry, rejected)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
