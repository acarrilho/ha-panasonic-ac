"""Polling coordinator for Panasonic Comfort Cloud air conditioners."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AcDevice,
    AgreementRequired,
    ApiError,
    CannotConnect,
    InvalidAuth,
    MfaRequired,
    PanasonicClient,
    PanasonicError,
)
from .const import (
    CONF_ENABLE_ENERGY,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_ENERGY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENERGY_INTERVAL,
    ISSUE_AGREEMENT_REQUIRED,
    REFRESH_AFTER_COMMAND,
)

_LOGGER = logging.getLogger(__name__)

type PanasonicConfigEntry = ConfigEntry[PanasonicCoordinator]


class PanasonicCoordinator(DataUpdateCoordinator[dict[str, AcDevice]]):
    """Polls every air conditioner on the account."""

    config_entry: PanasonicConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PanasonicConfigEntry, client: PanasonicClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.energy_enabled: bool = entry.options.get(CONF_ENABLE_ENERGY, DEFAULT_ENABLE_ENERGY)
        self._devices: dict[str, AcDevice] = {}
        self._energy_updated: datetime | None = None
        self._refresh_job = HassJob(self._delayed_refresh, cancel_on_shutdown=True)
        self._refresh_unsub = None

    # ------------------------------------------------------------ polling
    async def _async_setup(self) -> None:
        try:
            devices = await self.client.async_get_devices()
        except PanasonicError as err:
            raise self._translate(err) from err
        self._devices = {device.guid: device for device in devices}
        _LOGGER.debug("Found %d air conditioner(s)", len(devices))

    async def _async_update_data(self) -> dict[str, AcDevice]:
        errors: list[PanasonicError] = []
        for device in self._devices.values():
            try:
                await self.client.async_update_status(device)
            except (InvalidAuth, MfaRequired, AgreementRequired) as err:
                raise self._translate(err) from err
            except PanasonicError as err:
                _LOGGER.debug("Update failed for %s: %s", device.name, err)
                device.available = False
                errors.append(err)

        if self._devices and len(errors) == len(self._devices):
            raise UpdateFailed(f"Could not update any air conditioner: {errors[0]}")

        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_AGREEMENT_REQUIRED)

        if self.energy_enabled:
            await self._async_update_energy()
        return self._devices

    async def _async_update_energy(self) -> None:
        now = dt_util.now()
        if self._energy_updated and now - self._energy_updated < ENERGY_INTERVAL:
            return
        self._energy_updated = now
        for device in self._devices.values():
            if not device.has_energy or not device.available:
                continue
            try:
                device.energy_today = await self.client.async_get_energy_today(device, now)
            except PanasonicError as err:
                _LOGGER.debug("Energy update failed for %s: %s", device.name, err)

    def _translate(self, err: PanasonicError) -> Exception:
        """Map client errors to what Home Assistant expects."""
        if isinstance(err, (InvalidAuth, MfaRequired)):
            return ConfigEntryAuthFailed(str(err))
        if isinstance(err, AgreementRequired):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                ISSUE_AGREEMENT_REQUIRED,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_AGREEMENT_REQUIRED,
            )
            return UpdateFailed(str(err))
        if isinstance(err, (CannotConnect, ApiError)):
            return UpdateFailed(str(err))
        return UpdateFailed(str(err))

    # ----------------------------------------------------------- commands
    async def async_send(self, device: AcDevice, parameters: dict[str, Any]) -> None:
        """Send a command, update entities optimistically, re-poll shortly after."""
        _LOGGER.debug("Sending to %s: %s", device.name, parameters)
        try:
            await self.client.async_set_parameters(device, parameters)
        except (InvalidAuth, MfaRequired) as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except AgreementRequired as err:
            self._translate(err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="agreement_required"
            ) from err
        except PanasonicError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"device": device.name, "error": str(err)},
            ) from err
        self.async_update_listeners()
        self._schedule_refresh_after_command()

    @callback
    def _schedule_refresh_after_command(self) -> None:
        if self._refresh_unsub:
            self._refresh_unsub()
        self._refresh_unsub = async_call_later(self.hass, REFRESH_AFTER_COMMAND, self._refresh_job)

    async def _delayed_refresh(self, _now: datetime) -> None:
        self._refresh_unsub = None
        await self.async_refresh()

    async def async_shutdown(self) -> None:
        if self._refresh_unsub:
            self._refresh_unsub()
            self._refresh_unsub = None
        await super().async_shutdown()
