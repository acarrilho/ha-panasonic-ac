"""Tests for the standalone API client against the fake Panasonic server."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.panasonic_ac.api import (
    AgreementRequired,
    ApiError,
    EcoMode,
    FanSpeed,
    InvalidAuth,
    InvalidMfaCode,
    MfaRequired,
    OperationMode,
    StatusSource,
)
from custom_components.panasonic_ac.api.client import api_key, prepare_guid, utc_offset

from .conftest import client_for
from .fake_panasonic import CAC_GUID, OTP, RAC_GUID


async def test_signing_matches_reference() -> None:
    # Same algorithm as the two reference implementations
    key = api_key("2026-10-18 19:00:00", "token")
    assert key[9:12] == "cfc" and len(key) == 67


async def test_helpers() -> None:
    assert prepare_guid("A/B+C") == "AfB%2BC"
    assert utc_offset(datetime(2026, 10, 18, tzinfo=timezone(timedelta(hours=1)))) == "+01:00"
    assert utc_offset(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-3, minutes=-30)))) == "-03:30"


async def test_login_and_devices(fake, server, http) -> None:
    updates = []
    client = client_for(fake, http, on_session_update=updates.append)
    await client.async_login()
    session = client.session_data
    assert session["access_token"] and session["refresh_token"] and session["client_id"]
    assert updates[-1] == session

    devices = await client.async_get_devices()
    assert [d.guid for d in devices] == [RAC_GUID, CAC_GUID]  # Aquarea skipped
    rac, cac = devices
    assert rac.group == "Casa"

    await client.async_update_status(rac)
    assert rac.mode == OperationMode.HEAT
    assert rac.target_temperature == 21.5
    assert rac.inside_temperature == 19 and rac.outside_temperature == 9
    assert rac.fan_speed == FanSpeed.AUTO
    assert rac.supported_eco_modes == [EcoMode.NORMAL, EcoMode.POWERFUL, EcoMode.QUIET]
    assert rac.has_horizontal_swing and rac.has_nanoe and rac.has_inside_cleaning
    assert not rac.has_eco_navi and not rac.has_eco_function
    assert rac.min_temperature == 8  # summer house + heat mode
    assert rac.status_source == StatusSource.LIVE

    await client.async_update_status(cac)
    assert cac.inside_temperature is None  # 126 = not available
    assert cac.supported_fan_speeds == [FanSpeed.AUTO, FanSpeed.LOW, FanSpeed.MID, FanSpeed.HIGH]
    assert cac.supported_eco_modes == [EcoMode.NORMAL]
    assert not cac.has_horizontal_swing and not cac.has_nanoe and not cac.has_energy


async def test_wrong_password(fake, server, http) -> None:
    client = client_for(fake, http, password="nope")
    with pytest.raises(InvalidAuth, match="Wrong email or password"):
        await client.async_login()


@pytest.mark.parametrize("mfa", ["classic", "guardian"])
async def test_two_factor(fake, server, http, mfa) -> None:
    fake.mfa = mfa
    client = client_for(fake, http)
    with pytest.raises(MfaRequired):
        await client.async_login()
    assert client.mfa_pending
    with pytest.raises(InvalidMfaCode):
        await client.async_verify_mfa("000000")
    await client.async_verify_mfa(f" {OTP[:3]} {OTP[3:]} ")  # spaces are tolerated
    assert not client.mfa_pending
    assert len(await client.async_get_devices()) == 2


async def test_refresh_token_rotation_is_persisted(fake, server, http) -> None:
    fake.access_token_lifetime = 30  # always "expired" with the 60 s margin
    updates = []
    client = client_for(fake, http, on_session_update=updates.append)
    await client.async_login()
    first_refresh = client.session_data["refresh_token"]
    await client.async_get_devices()
    assert fake.refreshes == 1
    assert client.session_data["refresh_token"] != first_refresh
    assert updates[-1]["refresh_token"] == client.session_data["refresh_token"]


async def test_resume_from_stored_session(fake, server, http) -> None:
    client = client_for(fake, http)
    await client.async_login()
    stored = client.session_data

    resumed = client_for(fake, http, stored_session=stored)
    assert len(await resumed.async_get_devices()) == 2
    assert fake.logins == 1  # no second login


async def test_revoked_refresh_token_falls_back_to_login(fake, server, http) -> None:
    client = client_for(fake, http)
    await client.async_login()
    stored = client.session_data
    stored["expires_at"] = 0
    fake.revoke_refresh_tokens()  # e.g. invalid_grant after the app rotated it

    resumed = client_for(fake, http, stored_session=stored)
    assert len(await resumed.async_get_devices()) == 2
    assert fake.logins == 2


async def test_revoked_access_token_recovers(fake, server, http) -> None:
    client = client_for(fake, http)
    await client.async_login()
    fake.revoke_access_tokens()
    assert len(await client.async_get_devices()) == 2
    assert fake.refreshes == 1


async def test_revoked_everything_with_mfa_needs_user(fake, server, http) -> None:
    client = client_for(fake, http)
    await client.async_login()
    fake.mfa = "classic"
    fake.revoke_access_tokens()
    fake.revoke_refresh_tokens()
    with pytest.raises(MfaRequired):
        await client.async_get_devices()


async def test_outdated_app_version_is_redetected(fake, server, http) -> None:
    fake.required_app_version = "4.6.0"
    fake.play_store_version = "4.6.0"
    client = client_for(fake, http)
    await client.async_login()
    assert client.app_version == "4.6.0"
    assert len(await client.async_get_devices()) == 2


async def test_outdated_app_version_with_override(fake, server, http) -> None:
    fake.required_app_version = "4.6.0"  # Play Store gives nothing usable
    client = client_for(fake, http)
    with pytest.raises(ApiError):
        await client.async_login()
    client.set_app_version_override("4.6.0")
    await client.async_login()
    assert len(await client.async_get_devices()) == 2


async def test_agreements_block_until_accepted(fake, server, http) -> None:
    fake.agreement_pending = True
    client = client_for(fake, http)
    await client.async_login()
    with pytest.raises(AgreementRequired) as err:
        await client.async_get_devices()
    assert [a["type"] for a in err.value.pending] == [1, 2, 4]
    assert fake.accepted_agreements == []

    client.auto_accept_agreements = True
    assert len(await client.async_get_devices()) == 2
    assert sorted(a["type"] for a in fake.accepted_agreements) == [1, 2, 4]  # never type 3


async def test_live_status_falls_back_to_cache(fake, server, http) -> None:
    fake.live_status_fails = True
    client = client_for(fake, http)
    await client.async_login()
    rac = (await client.async_get_devices())[0]
    await client.async_update_status(rac)
    assert rac.status_source == StatusSource.CACHED
    fake.calls.clear()
    await client.async_update_status(rac)
    assert fake.calls == ["status_cached"]  # doesn't keep hammering the live endpoint


async def test_control_and_energy(fake, server, http) -> None:
    client = client_for(fake, http)
    await client.async_login()
    rac = (await client.async_get_devices())[0]
    await client.async_update_status(rac)
    await client.async_set_parameters(rac, {"operate": 1, "operationMode": 2, "temperatureSet": 23})
    assert fake.controls[-1] == {
        "deviceGuid": RAC_GUID,
        "parameters": {"operate": 1, "operationMode": 2, "temperatureSet": 23},
    }
    assert rac.mode == OperationMode.COOL and rac.target_temperature == 23

    now = datetime(2026, 10, 18, 20, 0, tzinfo=timezone(timedelta(hours=1)))
    assert await client.async_get_energy_today(rac, now) == 3.2
