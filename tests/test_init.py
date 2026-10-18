"""Setup, entities, commands and recovery inside Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.components.climate import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    ATTR_SWING_HORIZONTAL_MODE,
    ATTR_SWING_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panasonic_ac.const import CONF_SESSION, DOMAIN, ISSUE_AGREEMENT_REQUIRED

from .fake_panasonic import CAC_GUID, RAC_GUID

pytestmark = pytest.mark.usefixtures("patch_create_client")

LIVING = "climate.living_room"
DUCTED = "climate.ducted"


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="me@example.com",
        data={CONF_USERNAME: "me@example.com", CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _call(hass: HomeAssistant, service: str, data: dict, domain: str = CLIMATE_DOMAIN) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)


async def test_entities(hass: HomeAssistant, entry) -> None:
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_SESSION]["refresh_token"]  # tokens persisted

    state = hass.states.get(LIVING)
    assert state.state == HVACMode.HEAT
    attrs = state.attributes
    assert attrs["current_temperature"] == 19
    assert attrs[ATTR_TEMPERATURE] == 21.5
    assert attrs["min_temp"] == 8 and attrs["max_temp"] == 30
    assert attrs["hvac_modes"] == ["off", "heat_cool", "cool", "heat", "dry", "fan_only"]
    assert attrs["fan_modes"] == ["auto", "low", "medium_low", "medium", "medium_high", "high"]
    assert attrs["preset_modes"] == ["none", "boost", "quiet"]
    assert attrs[ATTR_SWING_MODE] == "mid"
    assert attrs[ATTR_SWING_HORIZONTAL_MODE] == "mid"

    ducted = hass.states.get(DUCTED)
    assert ducted.state == HVACMode.OFF
    assert ducted.attributes["fan_modes"] == ["auto", "low", "medium", "high"]
    assert "preset_modes" not in ducted.attributes
    assert ATTR_SWING_HORIZONTAL_MODE not in ducted.attributes
    assert ducted.attributes[ATTR_SWING_MODE] == "auto"
    assert ducted.attributes["current_temperature"] is None

    assert hass.states.get("sensor.living_room_inside_temperature").state == "19.0"
    assert hass.states.get("sensor.living_room_outside_temperature").state == "9.0"
    assert hass.states.get("sensor.living_room_energy_today").state == "3.2"
    assert hass.states.get("sensor.ducted_outside_temperature") is None  # unit reports 126
    assert hass.states.get("sensor.ducted_energy_today") is None  # no power metering
    assert hass.states.get("switch.living_room_nanoe").state == "off"
    assert hass.states.get("switch.living_room_inside_cleaning").state == "off"
    assert hass.states.get("switch.ducted_nanoe") is None


async def test_commands(hass: HomeAssistant, entry, fake) -> None:
    await _call(hass, "set_hvac_mode", {ATTR_ENTITY_ID: LIVING, ATTR_HVAC_MODE: HVACMode.COOL})
    assert fake.controls[-1]["parameters"] == {"operate": 1, "operationMode": 2}
    assert hass.states.get(LIVING).state == HVACMode.COOL  # optimistic

    await _call(hass, "set_temperature", {ATTR_ENTITY_ID: LIVING, ATTR_TEMPERATURE: 23.3})
    assert fake.controls[-1]["parameters"] == {"temperatureSet": 23.5}

    await _call(hass, "set_fan_mode", {ATTR_ENTITY_ID: LIVING, ATTR_FAN_MODE: "medium_high"})
    assert fake.controls[-1]["parameters"] == {"fanSpeed": 4}

    await _call(hass, "set_preset_mode", {ATTR_ENTITY_ID: LIVING, ATTR_PRESET_MODE: "quiet"})
    assert fake.controls[-1]["parameters"] == {"ecoMode": 2}

    # vertical auto while horizontal is fixed -> fanAutoMode UD (2)
    await _call(hass, "set_swing_mode", {ATTR_ENTITY_ID: LIVING, ATTR_SWING_MODE: "auto"})
    assert fake.controls[-1]["parameters"] == {"fanAutoMode": 2}
    # then horizontal auto too -> BOTH (0)
    await _call(hass, "set_swing_horizontal_mode", {ATTR_ENTITY_ID: LIVING, ATTR_SWING_HORIZONTAL_MODE: "auto"})
    assert fake.controls[-1]["parameters"] == {"fanAutoMode": 0}
    # fixed vertical position keeps horizontal auto -> LR (3)
    await _call(hass, "set_swing_mode", {ATTR_ENTITY_ID: LIVING, ATTR_SWING_MODE: "down"})
    assert fake.controls[-1]["parameters"] == {"fanAutoMode": 3, "airSwingUD": 1}
    state = hass.states.get(LIVING)
    assert state.attributes[ATTR_SWING_MODE] == "down"
    assert state.attributes[ATTR_SWING_HORIZONTAL_MODE] == "auto"

    await _call(hass, "turn_on", {ATTR_ENTITY_ID: "switch.living_room_nanoe"}, domain="switch")
    assert fake.controls[-1]["parameters"] == {"nanoe": 2}
    assert hass.states.get("switch.living_room_nanoe").state == "on"

    await _call(hass, "turn_off", {ATTR_ENTITY_ID: LIVING})
    assert fake.controls[-1] == {"deviceGuid": RAC_GUID, "parameters": {"operate": 0}}
    assert hass.states.get(LIVING).state == HVACMode.OFF

    await _call(hass, "set_hvac_mode", {ATTR_ENTITY_ID: DUCTED, ATTR_HVAC_MODE: HVACMode.HEAT})
    assert fake.controls[-1] == {"deviceGuid": CAC_GUID, "parameters": {"operate": 1, "operationMode": 3}}

    # the delayed re-poll reads back what the "device" now reports
    coordinator = entry.runtime_data
    await coordinator.async_refresh()
    assert hass.states.get(DUCTED).state == HVACMode.HEAT


async def test_recovers_from_revoked_tokens(hass: HomeAssistant, entry, fake) -> None:
    fake.revoke_access_tokens()
    fake.revoke_refresh_tokens()
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success
    assert fake.logins == 2  # silently logged in again with the stored password
    assert hass.states.get(LIVING).state == HVACMode.HEAT


async def test_reauth_when_login_needs_2fa(hass: HomeAssistant, entry, fake) -> None:
    fake.mfa = "classic"
    fake.revoke_access_tokens()
    fake.revoke_refresh_tokens()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_command_failure_raises(hass: HomeAssistant, entry, fake) -> None:
    fake.statuses.pop(RAC_GUID)  # make the control endpoint fail for this unit
    with pytest.raises(HomeAssistantError, match="Could not send the command to Living room"):
        await _call(hass, "set_fan_mode", {ATTR_ENTITY_ID: LIVING, ATTR_FAN_MODE: "high"})


async def test_agreement_issue(hass: HomeAssistant, entry, fake) -> None:
    coordinator = entry.runtime_data
    issues = ir.async_get(hass)
    fake.agreement_pending = True  # Panasonic publishes new terms mid-operation

    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert issues.async_get_issue(DOMAIN, ISSUE_AGREEMENT_REQUIRED)
    assert fake.accepted_agreements == []  # never accepted without permission

    fake.agreement_pending = False  # user accepted them in the app
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert issues.async_get_issue(DOMAIN, ISSUE_AGREEMENT_REQUIRED) is None


async def test_agreement_auto_accept(hass: HomeAssistant, entry, fake) -> None:
    coordinator = entry.runtime_data
    coordinator.client.auto_accept_agreements = True
    fake.agreement_pending = True
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert sorted(a["type"] for a in fake.accepted_agreements) == [1, 2, 4]


async def test_diagnostics_redact(hass: HomeAssistant, entry) -> None:
    from custom_components.panasonic_ac.diagnostics import async_get_config_entry_diagnostics

    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diag)
    assert "secret" not in text and "me@example.com" not in text and RAC_GUID not in text
    assert entry.data[CONF_SESSION]["refresh_token"] not in text
    assert len(diag["devices"]) == 2
