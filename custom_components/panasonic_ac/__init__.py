"""Panasonic Comfort Cloud integration for air conditioners."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import PanasonicClient
from .const import CONF_APP_VERSION, CONF_AUTO_ACCEPT_AGREEMENTS, CONF_SESSION
from .coordinator import PanasonicConfigEntry, PanasonicCoordinator

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR, Platform.SWITCH]


def create_client(
    hass: HomeAssistant,
    username: str,
    password: str,
    *,
    stored_session: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    on_session_update=None,
) -> PanasonicClient:
    """Build a client with its own HTTP session (and therefore its own cookie jar)."""
    options = options or {}
    return PanasonicClient(
        async_create_clientsession(hass),
        username,
        password,
        stored_session=stored_session,
        app_version=options.get(CONF_APP_VERSION),
        auto_accept_agreements=options.get(CONF_AUTO_ACCEPT_AGREEMENTS, False),
        on_session_update=on_session_update,
    )


async def async_setup_entry(hass: HomeAssistant, entry: PanasonicConfigEntry) -> bool:
    """Set up Panasonic Comfort Cloud from a config entry."""

    @callback
    def _persist_session(session: dict[str, Any]) -> None:
        # Refresh tokens rotate: persist every new one so a restart never reuses a dead token.
        if entry.data.get(CONF_SESSION) != session:
            hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_SESSION: session})

    client = create_client(
        hass,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        stored_session=entry.data.get(CONF_SESSION),
        options=dict(entry.options),
        on_session_update=_persist_session,
    )
    coordinator = PanasonicCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Reload only when the options change (session updates also touch the entry).
    options_snapshot = dict(entry.options)

    async def _async_options_updated(hass: HomeAssistant, updated: PanasonicConfigEntry) -> None:
        if dict(updated.options) != options_snapshot:
            await hass.config_entries.async_reload(updated.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PanasonicConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
