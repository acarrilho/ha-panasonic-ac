"""Base entity for Panasonic Comfort Cloud air conditioners."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import AcDevice
from .const import DOMAIN, MANUFACTURER
from .coordinator import PanasonicCoordinator


class PanasonicEntity(CoordinatorEntity[PanasonicCoordinator]):
    """An entity belonging to one indoor unit."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PanasonicCoordinator, device: AcDevice, key: str) -> None:
        super().__init__(coordinator)
        self._guid = device.guid
        self._attr_unique_id = f"{device.guid}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.guid)},
            name=device.name,
            manufacturer=MANUFACTURER,
            model=device.model or None,
        )

    @property
    def device(self) -> AcDevice:
        return self.coordinator.data[self._guid]

    @property
    def available(self) -> bool:
        return super().available and self._guid in self.coordinator.data and self.device.available

    async def _send(self, parameters: dict[str, Any]) -> None:
        await self.coordinator.async_send(self.device, parameters)
