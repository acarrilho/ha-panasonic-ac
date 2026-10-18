#!/usr/bin/env python3
"""Check your Panasonic Comfort Cloud login outside Home Assistant.

Usage (needs Python 3.12+ and aiohttp):
    pip install aiohttp
    python scripts/check_login.py            # prompts for email/password (and 2FA code)
    python scripts/check_login.py --debug    # also prints every HTTP step

Nothing is stored; the script only reads the device list and status.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from pathlib import Path

import aiohttp

# Import the integration's API package directly (it has no Home Assistant imports)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "panasonic_ac"))
from api import AgreementRequired, MfaRequired, PanasonicClient, PanasonicError  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", help="Panasonic ID (email)")
    parser.add_argument("--app-version", help="Override the Comfort Cloud app version (e.g. 4.4.0)")
    parser.add_argument("--debug", action="store_true", help="Verbose HTTP logging")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(levelname)s %(message)s")

    username = args.username or (await asyncio.to_thread(input, "Panasonic ID (email): ")).strip()
    password = await asyncio.to_thread(getpass.getpass, "Password: ")

    async with aiohttp.ClientSession() as session:
        client = PanasonicClient(session, username, password, app_version=args.app_version)
        try:
            try:
                await client.async_login()
            except MfaRequired:
                code = await asyncio.to_thread(input, "2FA verification code: ")
                await client.async_verify_mfa(code.strip())
            print(f"\nLogged in (app version {client.app_version}).")

            devices = await client.async_get_devices()
            print(f"Found {len(devices)} air conditioner(s):\n")
            for device in devices:
                await client.async_update_status(device)
                print(
                    f"  {device.name} [{device.model}] in '{device.group}': "
                    f"power={device.power.name} mode={device.mode.name if device.mode else '?'} "
                    f"target={device.target_temperature} inside={device.inside_temperature} "
                    f"outside={device.outside_temperature} ({device.status_source.name.lower()} data)"
                )
        except AgreementRequired as err:
            print(f"\n{err}\nPending: {err.pending}")
            return 2
        except PanasonicError as err:
            print(f"\nFailed: {type(err).__name__}: {err}")
            return 1
    print("\nEverything works. You can install the integration in Home Assistant.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
