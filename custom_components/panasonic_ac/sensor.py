"""Sensors for Panasonic Comfort Cloud air conditioners."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AcDevice, StatusSource
from .coordinator import PanasonicConfigEntry, PanasonicCoordinator
from .entity import PanasonicEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PanasonicSensorDescription(SensorEntityDescription):
    value_fn: Callable[[AcDevice], float | str | None]
    exists_fn: Callable[[PanasonicCoordinator, AcDevice], bool] = lambda _c, _d: True


SENSORS: tuple[PanasonicSensorDescription, ...] = (
    PanasonicSensorDescription(
        key="inside_temperature",
        translation_key="inside_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.inside_temperature,
    ),
    PanasonicSensorDescription(
        key="outside_temperature",
        translation_key="outside_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.outside_temperature,
        exists_fn=lambda _c, d: d.outside_temperature is not None,
    ),
    PanasonicSensorDescription(
        key="energy_today",
        translation_key="energy_today",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda d: d.energy_today,
        exists_fn=lambda c, d: c.energy_enabled and d.has_energy,
    ),
    PanasonicSensorDescription(
        key="data_source",
        translation_key="data_source",
        device_class=SensorDeviceClass.ENUM,
        options=["live", "cached"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: "live" if d.status_source == StatusSource.LIVE else "cached",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        PanasonicSensor(coordinator, device, description)
        for device in coordinator.data.values()
        for description in SENSORS
        if description.exists_fn(coordinator, device)
    )


class PanasonicSensor(PanasonicEntity, SensorEntity):
    entity_description: PanasonicSensorDescription

    def __init__(
        self, coordinator: PanasonicCoordinator, device: AcDevice, description: PanasonicSensorDescription
    ) -> None:
        super().__init__(coordinator, device, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        return self.entity_description.value_fn(self.device)
