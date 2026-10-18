"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from custom_components.panasonic_ac.api import PanasonicClient

from .fake_panasonic import FakePanasonic, start


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake() -> FakePanasonic:
    return FakePanasonic()


@pytest.fixture
async def server(fake: FakePanasonic, socket_enabled) -> AsyncGenerator[str]:
    runner, base = await start(fake)
    yield base
    await runner.cleanup()


def client_for(fake: FakePanasonic, session: aiohttp.ClientSession, **kwargs: Any) -> PanasonicClient:
    kwargs.setdefault("username", fake.username)
    kwargs.setdefault("password", fake.password)
    username = kwargs.pop("username")
    password = kwargs.pop("password")
    return PanasonicClient(
        session,
        username,
        password,
        auth_base=fake.base_url,
        api_base=fake.base_url,
        play_store_url=f"{fake.base_url}/store/apps/details",
        **kwargs,
    )


@pytest.fixture
async def http() -> AsyncGenerator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
def patch_create_client(fake: FakePanasonic, server: str):
    """Make the integration talk to the fake server instead of Panasonic."""
    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    from custom_components.panasonic_ac.const import CONF_APP_VERSION, CONF_AUTO_ACCEPT_AGREEMENTS

    def _create(hass, username, password, *, stored_session=None, options=None, on_session_update=None):
        options = options or {}
        return client_for(
            fake,
            async_create_clientsession(hass),
            username=username,
            password=password,
            stored_session=stored_session,
            app_version=options.get(CONF_APP_VERSION),
            auto_accept_agreements=options.get(CONF_AUTO_ACCEPT_AGREEMENTS, False),
            on_session_update=on_session_update,
        )

    with (
        patch("custom_components.panasonic_ac.create_client", _create),
        patch("custom_components.panasonic_ac.config_flow.create_client", _create),
    ):
        yield
