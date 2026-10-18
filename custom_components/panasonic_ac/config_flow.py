"""Config flow: login (with 2FA), terms acceptance, reauth and options."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import create_client
from .api import (
    AgreementRequired,
    ApiError,
    CannotConnect,
    InvalidAuth,
    InvalidMfaCode,
    MfaRequired,
    PanasonicClient,
    PanasonicError,
)
from .const import (
    CONF_APP_VERSION,
    CONF_AUTO_ACCEPT_AGREEMENTS,
    CONF_ENABLE_ENERGY,
    CONF_SCAN_INTERVAL,
    CONF_SESSION,
    DEFAULT_ENABLE_ENERGY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import PanasonicConfigEntry

_LOGGER = logging.getLogger(__name__)

CONF_CODE = "code"
CONF_ACCEPT = "accept"

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)
MFA_SCHEMA = vol.Schema({vol.Required(CONF_CODE): str})
AGREEMENT_SCHEMA = vol.Schema({vol.Required(CONF_ACCEPT, default=False): bool})


class PanasonicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Panasonic Comfort Cloud."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: PanasonicClient | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._auto_accept = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: PanasonicConfigEntry) -> PanasonicOptionsFlow:
        return PanasonicOptionsFlow()

    # ------------------------------------------------------------------ steps
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(self._username.lower())
            if self.source != SOURCE_REAUTH:
                self._abort_if_unique_id_configured()
            return await self._async_login(errors, placeholders, "user")

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, {CONF_USERNAME: self._username or ""}),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._username = entry_data[CONF_USERNAME]
        self._auto_accept = self._get_reauth_entry().options.get(CONF_AUTO_ACCEPT_AGREEMENTS, False)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders = {"username": self._username or ""}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            return await self._async_login(errors, placeholders, "reauth_confirm")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
                    )
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_mfa(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and self._client is not None:
            try:
                await self._client.async_verify_mfa(user_input[CONF_CODE])
            except InvalidMfaCode:
                errors["base"] = "invalid_code"
            except InvalidAuth:
                # Challenge expired: start over from the credentials step
                errors["base"] = "mfa_expired"
                return self._show_credentials_form(errors)
            except PanasonicError as err:
                _LOGGER.warning("2FA verification failed: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_finish()
        return self.async_show_form(step_id="mfa", data_schema=MFA_SCHEMA, errors=errors)

    async def async_step_agreements(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_ACCEPT]:
                errors["base"] = "agreement_not_accepted"
            elif self._client is not None:
                self._auto_accept = True
                self._client.auto_accept_agreements = True
                try:
                    pending = await self._client.async_get_pending_agreements()
                    if pending:
                        await self._client.async_accept_agreements(pending)
                except PanasonicError as err:
                    _LOGGER.warning("Could not accept agreements: %s", err)
                    errors["base"] = "agreement_failed"
                else:
                    return await self._async_finish()
        return self.async_show_form(step_id="agreements", data_schema=AGREEMENT_SCHEMA, errors=errors)

    # ---------------------------------------------------------------- helpers
    def _show_credentials_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        if self.source == SOURCE_REAUTH:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
                errors=errors,
                description_placeholders={"username": self._username or ""},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, {CONF_USERNAME: self._username or ""}),
            errors=errors,
        )

    async def _async_login(
        self, errors: dict[str, str], placeholders: dict[str, str], step_id: str
    ) -> ConfigFlowResult:
        assert self._username is not None and self._password is not None
        options = {CONF_AUTO_ACCEPT_AGREEMENTS: self._auto_accept}
        if self.source == SOURCE_REAUTH:
            options = dict(self._get_reauth_entry().options)
        self._client = create_client(self.hass, self._username, self._password, options=options)
        try:
            await self._client.async_login()
        except MfaRequired:
            return await self.async_step_mfa()
        except InvalidAuth as err:
            _LOGGER.debug("Login rejected: %s", err)
            errors["base"] = "invalid_auth"
        except (CannotConnect, ApiError) as err:
            _LOGGER.warning("Login failed: %s", err)
            errors["base"] = "cannot_connect"
        except PanasonicError:
            _LOGGER.exception("Unexpected login error")
            errors["base"] = "unknown"
        else:
            return await self._async_finish()
        return self._show_credentials_form(errors)

    async def _async_finish(self) -> ConfigFlowResult:
        """Logged in: check the account works, then create or update the entry."""
        assert self._client is not None
        try:
            devices = await self._client.async_get_devices()
        except AgreementRequired:
            return await self.async_step_agreements()
        except PanasonicError as err:
            _LOGGER.warning("Could not list devices: %s", err)
            return self._show_credentials_form({"base": "cannot_connect"})

        data = {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_SESSION: self._client.session_data,
        }
        if self.source == SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            options = dict(entry.options)
            if self._auto_accept:
                options[CONF_AUTO_ACCEPT_AGREEMENTS] = True
            return self.async_update_reload_and_abort(entry, data=data, options=options)

        if not devices:
            return self.async_abort(reason="no_devices")
        return self.async_create_entry(
            title=self._username or "Panasonic Comfort Cloud",
            data=data,
            options={CONF_AUTO_ACCEPT_AGREEMENTS: self._auto_accept},
        )


class PanasonicOptionsFlow(OptionsFlow):
    """Polling, energy, terms and app-version options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            user_input[CONF_SCAN_INTERVAL] = int(user_input[CONF_SCAN_INTERVAL])
            user_input[CONF_APP_VERSION] = (user_input.get(CONF_APP_VERSION) or "").strip()
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=10,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_ENABLE_ENERGY, default=options.get(CONF_ENABLE_ENERGY, DEFAULT_ENABLE_ENERGY)
                ): bool,
                vol.Required(
                    CONF_AUTO_ACCEPT_AGREEMENTS, default=options.get(CONF_AUTO_ACCEPT_AGREEMENTS, False)
                ): bool,
                vol.Optional(
                    CONF_APP_VERSION, description={"suggested_value": options.get(CONF_APP_VERSION, "")}
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
