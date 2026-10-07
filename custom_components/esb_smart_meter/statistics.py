"""Write ESB history into Home Assistant long-term (external) statistics."""

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(DOMAIN)


async def _stored_sum(hass: HomeAssistant, statistic_id: str, hour: datetime) -> float | None:
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        hour,
        hour + timedelta(hours=1),
        {statistic_id},
        "hour",
        None,
        {"sum"},
    )
    rows = result.get(statistic_id) or []
    return rows[0].get("sum") if rows else None


async def async_write_statistics(
    hass: HomeAssistant,
    statistic_id: str,
    name: str,
    hourly: list[tuple[datetime, float]],
) -> int:
    """Write hourly kWh totals as a cumulative-sum external statistic.

    ESB only returns a rolling ~2 year window. Restarting the running sum from 0
    each time would leave a cliff where the window used to start, so the sum is
    anchored to what the recorder already holds for the window's first hour.
    """
    if not hourly:
        return 0

    accumulated = 0.0
    stored = await _stored_sum(hass, statistic_id, hourly[0][0])
    if stored is not None:
        # Keep the stored first hour as-is and continue from it.
        accumulated = stored
        hourly = hourly[1:]

    stat_data = []
    for start, kwh in hourly:
        accumulated += kwh
        stat_data.append(StatisticData(start=start, state=kwh, sum=accumulated))

    meta = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        unit_class="energy",
    )
    if stat_data:
        async_add_external_statistics(hass, meta, stat_data)
    _LOGGER.info("ESB: wrote %d hourly stat entries to %s", len(stat_data), statistic_id)
    return len(stat_data)
