import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_MPRN, DOMAIN
from .coordinator import ESBCoordinator, session_cache_path, update_interval

_LOGGER = logging.getLogger(DOMAIN)
PLATFORMS = ["sensor"]

type ESBConfigEntry = ConfigEntry[ESBCoordinator]


def _migrate_session_cache(hass: HomeAssistant, mprn: str) -> None:
    """Move the pre-1.1 shared session cache to its per-MPRN name (saves a login)."""
    old = hass.config.path(".storage", "esb_session_cache.json")
    new = session_cache_path(hass, mprn)
    if os.path.exists(old) and not os.path.exists(new):
        os.replace(old, new)
        _LOGGER.info("ESB: moved session cache to %s", new)


async def async_setup_entry(hass: HomeAssistant, entry: ESBConfigEntry) -> bool:
    await hass.async_add_executor_job(_migrate_session_cache, hass, entry.data[CONF_MPRN])

    coordinator = ESBCoordinator(hass, entry)
    await coordinator.async_load()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Logging in to ESB can take a while (and may be rate limited), so don't
    # hold up Home Assistant's startup waiting for the first fetch.
    entry.async_create_background_task(hass, coordinator.async_refresh(), f"{DOMAIN} first refresh")
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ESBConfigEntry) -> None:
    # Only the polling interval is an option; apply it without reloading (and re-fetching).
    entry.runtime_data.update_interval = update_interval(entry)


async def async_unload_entry(hass: HomeAssistant, entry: ESBConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
