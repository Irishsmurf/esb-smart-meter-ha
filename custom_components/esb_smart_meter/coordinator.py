"""Fetch ESB data on a schedule and push it into long-term statistics."""

import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import ESBAuthError, ESBError, ESBNetworksAPI, parse_csv
from .const import (
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    FAILURE_ISSUE_AFTER_HOURS,
)
from .processing import hourly_totals, last_complete_day, latest_reading_end
from .statistics import async_write_statistics

_LOGGER = logging.getLogger(DOMAIN)


def clean_mprn(mprn: str) -> str:
    return mprn.replace(" ", "").strip()


def session_cache_path(hass: HomeAssistant, mprn: str) -> str:
    return hass.config.path(".storage", f"{DOMAIN}_session_{clean_mprn(mprn)}.json")


def update_interval(entry: ConfigEntry) -> timedelta:
    return timedelta(hours=entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS))


@dataclass
class ESBData:
    last_success: datetime | None = None
    latest_reading: datetime | None = None
    last_day: date | None = None
    last_day_kwh: float | None = None
    last_day_export_kwh: float | None = None
    has_export: bool = False

    def as_dict(self) -> dict:
        data = asdict(self)
        for key in ("last_success", "latest_reading", "last_day"):
            if data[key] is not None:
                data[key] = data[key].isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ESBData":
        data = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        for key in ("last_success", "latest_reading"):
            if data.get(key):
                data[key] = dt_util.parse_datetime(data[key])
        if data.get("last_day"):
            data["last_day"] = date.fromisoformat(data["last_day"])
        return cls(**data)


class ESBCoordinator(DataUpdateCoordinator[ESBData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=update_interval(entry),
        )
        self.mprn = clean_mprn(entry.data[CONF_MPRN])
        self.api = ESBNetworksAPI(
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            self.mprn,
            cache_path=session_cache_path(hass, self.mprn),
        )
        self.last_error: str | None = None
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}_data_{entry.entry_id}")

    async def async_load(self) -> None:
        """Restore the last good data so sensors survive a restart without a fetch."""
        if stored := await self._store.async_load():
            self.data = ESBData.from_dict(stored)

    @property
    def consumption_statistic_id(self) -> str:
        return f"{DOMAIN}:consumption_{self.mprn}"

    @property
    def export_statistic_id(self) -> str:
        return f"{DOMAIN}:export_{self.mprn}"

    @property
    def _issue_id(self) -> str:
        return f"update_failed_{self.config_entry.entry_id}"

    async def _async_update_data(self) -> ESBData:
        previous = self.data or ESBData()
        try:
            csv_text = await self.hass.async_add_executor_job(self.api.download_csv)
            parsed = await self.hass.async_add_executor_job(parse_csv, csv_text)
            if not parsed.imports:
                raise ESBError("no consumption readings in the downloaded CSV")
        except ESBAuthError as e:
            # Starts the re-authentication flow in the UI.
            raise ConfigEntryAuthFailed(str(e)) from e
        except ESBError as e:
            # Keep showing the last good data; flag it if this has gone on too long.
            self.last_error = str(e)
            _LOGGER.warning("ESB: update failed: %s", e)
            self._report_failure(previous)
            return previous

        await async_write_statistics(
            self.hass,
            self.consumption_statistic_id,
            "ESB Smart Meter Consumption",
            hourly_totals(parsed.imports),
        )
        if parsed.exports:
            await async_write_statistics(
                self.hass,
                self.export_statistic_id,
                "ESB Smart Meter Export",
                hourly_totals(parsed.exports),
            )

        self.last_error = None
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

        data = ESBData(
            last_success=dt_util.utcnow(),
            latest_reading=latest_reading_end(parsed.imports),
            has_export=bool(parsed.exports),
        )
        if day := last_complete_day(parsed.imports):
            data.last_day, data.last_day_kwh = day
            export_day = last_complete_day(parsed.exports)
            if export_day and export_day[0] == data.last_day:
                data.last_day_export_kwh = export_day[1]
        await self._store.async_save(data.as_dict())
        _LOGGER.info(
            "ESB: update complete. Latest reading %s, %s: %s kWh",
            data.latest_reading, data.last_day, data.last_day_kwh,
        )
        return data

    def _report_failure(self, previous: ESBData) -> None:
        last_success = previous.last_success
        if last_success and dt_util.utcnow() - last_success < timedelta(hours=FAILURE_ISSUE_AFTER_HOURS):
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="update_failed",
            translation_placeholders={
                "error": self.last_error or "unknown error",
                "last_success": last_success.isoformat(timespec="minutes") if last_success else "never",
            },
        )
