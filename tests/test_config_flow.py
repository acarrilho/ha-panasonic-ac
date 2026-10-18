"""Config flow tests (run against the fake Panasonic server)."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.panasonic_ac.const import (
    CONF_APP_VERSION,
    CONF_AUTO_ACCEPT_AGREEMENTS,
    CONF_ENABLE_ENERGY,
    CONF_SCAN_INTERVAL,
    CONF_SESSION,
    DOMAIN,
)

from .fake_panasonic import OTP

pytestmark = pytest.mark.usefixtures("patch_create_client")

CREDS = {CONF_USERNAME: "me@example.com", CONF_PASSWORD: "secret"}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_user_flow(hass: HomeAssistant, fake) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "me@example.com"
    assert result["data"][CONF_PASSWORD] == "secret"
    assert result["data"][CONF_SESSION]["refresh_token"]
    assert result["options"] == {CONF_AUTO_ACCEPT_AGREEMENTS: False}
    assert result["result"].unique_id == "me@example.com"


async def test_invalid_auth(hass: HomeAssistant) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CREDS, CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize("mfa", ["classic", "guardian"])
async def test_two_factor_flow(hass: HomeAssistant, fake, mfa) -> None:
    fake.mfa = mfa
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "mfa"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "111111"})
    assert result["errors"] == {"base": "invalid_code"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": OTP})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_agreements_step(hass: HomeAssistant, fake) -> None:
    fake.agreement_pending = True
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["step_id"] == "agreements"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"accept": False})
    assert result["errors"] == {"base": "agreement_not_accepted"}
    assert fake.accepted_agreements == []

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"accept": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_AUTO_ACCEPT_AGREEMENTS: True}
    assert sorted(a["type"] for a in fake.accepted_agreements) == [1, 2, 4]


async def test_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id="me@example.com", data=CREDS).add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CREDS, CONF_USERNAME: "Me@Example.com"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth_with_new_password(hass: HomeAssistant, fake) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="me@example.com", data={**CREDS, CONF_PASSWORD: "old"})
    entry.add_to_hass(hass)
    fake.password = "new-password"
    fake.mfa = "classic"

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new-password"})
    assert result["step_id"] == "mfa"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": OTP})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data[CONF_SESSION]["refresh_token"]
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id="me@example.com", data=CREDS)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 60,
            CONF_ENABLE_ENERGY: False,
            CONF_AUTO_ACCEPT_AGREEMENTS: True,
            CONF_APP_VERSION: " 4.5.1 ",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SCAN_INTERVAL: 60,
        CONF_ENABLE_ENERGY: False,
        CONF_AUTO_ACCEPT_AGREEMENTS: True,
        CONF_APP_VERSION: "4.5.1",
    }
