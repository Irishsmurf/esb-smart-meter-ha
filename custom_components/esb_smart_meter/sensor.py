from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ESBConfigEntry
from .const import DOMAIN, NAME
from .coordinator import ESBCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ESBConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator = entry.runtime_data
    async_add_entities([
        LastDayConsumptionSensor(coordinator),
        LatestReadingSensor(coordinator),
        LastSuccessSensor(coordinator),
    ])

    # Only meters with microgeneration report export; add the sensor once we see it.
    export_added = False

    @callback
    def _maybe_add_export() -> None:
        nonlocal export_added
        if not export_added and coordinator.data and coordinator.data.has_export:
            export_added = True
            async_add_entities([LastDayExportSensor(coordinator)])

    _maybe_add_export()
    entry.async_on_unload(coordinator.async_add_listener(_maybe_add_export))


class ESBSensor(CoordinatorEntity[ESBCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ESBCoordinator, key: str):
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{DOMAIN}_{coordinator.mprn}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mprn)},
            name=NAME,
            manufacturer="ESB Networks",
            model="Smart meter",
            serial_number=coordinator.mprn,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        # Failed fetches keep the previous data, so show it rather than "unavailable".
        return self.coordinator.data is not None


class LastDayConsumptionSensor(ESBSensor):
    """Consumption for the most recent complete day.

    Deliberately has no state_class: it is a per-day figure, not a meter, and the
    Energy dashboard should use the ``esb_smart_meter:consumption_<MPRN>``
    statistic instead, which carries the correct hourly timestamps.
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:lightning-bolt"

    def __init__(self, coordinator: ESBCoordinator):
        super().__init__(coordinator, "last_day_consumption")
        # Keep the pre-1.1 unique ID so the existing entity (and its entity_id) carry over.
        self._attr_unique_id = f"{DOMAIN}_{coordinator.mprn}"

    @property
    def native_value(self):
        return self.coordinator.data.last_day_kwh

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {
            "mprn": self.coordinator.mprn,
            "date": data.last_day.isoformat() if data.last_day else None,
            "statistic_id": self.coordinator.consumption_statistic_id,
        }


class LastDayExportSensor(ESBSensor):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:solar-power"

    def __init__(self, coordinator: ESBCoordinator):
        super().__init__(coordinator, "last_day_export")

    @property
    def native_value(self):
        return self.coordinator.data.last_day_export_kwh

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {
            "date": data.last_day.isoformat() if data.last_day else None,
            "statistic_id": self.coordinator.export_statistic_id,
        }


class LatestReadingSensor(ESBSensor):
    """End of the newest half-hour ESB has published (typically 1-3 days ago)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ESBCoordinator):
        super().__init__(coordinator, "latest_reading")

    @property
    def native_value(self):
        return self.coordinator.data.latest_reading


class LastSuccessSensor(ESBSensor):
    """When data was last downloaded successfully. Goes stale if logins are blocked."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ESBCoordinator):
        super().__init__(coordinator, "last_success")

    @property
    def native_value(self):
        return self.coordinator.data.last_success

    @property
    def extra_state_attributes(self):
        return {"last_error": self.coordinator.last_error}
