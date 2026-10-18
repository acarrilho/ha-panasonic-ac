"""Diagnostics: raw device data with credentials and tokens removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_SESSION
from .coordinator import PanasonicConfigEntry

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, CONF_SESSION, "deviceGuid", "deviceHashGuid", "guid"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PanasonicConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    devices = []
    for index, device in enumerate(coordinator.data.values()):
        devices.append(
            {
                "index": index,
                "model": device.model,
                "device_type": device.device_type,
                "available": device.available,
                "status_source": device.status_source.name,
                "timestamp": device.timestamp.isoformat() if device.timestamp else None,
                "energy_today": device.energy_today,
                "features": async_redact_data(device.features, TO_REDACT),
                "parameters": async_redact_data(device.params, TO_REDACT),
            }
        )
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "app_version": coordinator.client.app_version,
        "devices": devices,
    }
