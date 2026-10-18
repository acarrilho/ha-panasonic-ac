# Panasonic Comfort Cloud (AC) for Home Assistant

A lean, self-contained Home Assistant integration for **Panasonic air conditioners** connected to Panasonic Comfort Cloud. It has its own API client with no third-party Python dependencies. It was built to keep working through the Panasonic-side changes that repeatedly broke other integrations in 2026.

Scope: air conditioners only (room units and ducted/commercial units). Aquarea heat pumps and HWS hot-water tanks on the same account are ignored.

## What it does

| Entity | Details |
|---|---|
| Climate | Off / Heat-Cool (Panasonic "Auto") / Cool / Heat / Dry / Fan; target temperature in 0.5 °C steps (8 °C minimum in Heat when the unit supports the "summer house" range); fan speed (Auto + 5 steps, or what a ducted unit allows); presets Normal / Powerful / Quiet; vertical **and** horizontal louver position as separate controls |
| Sensors | Inside temperature, outside temperature (if the unit reports it), energy today (kWh, usable in the Energy dashboard), data source live/cached (diagnostic, disabled by default) |
| Switches | nanoe, ECONAVI, AI ECO, inside cleaning — only created when the unit supports them |
| Diagnostics | Downloadable, with credentials, tokens and device IDs removed |

Interface languages: English and Portuguese.

## Why it's more robust

These are the ways Comfort Cloud integrations have broken, and how this one handles each:

- **"Refresh token invalid" / entities going unavailable until reload.** Panasonic rotates refresh tokens. Every new token is saved to the config entry immediately, so a restart never reuses a dead one. If Panasonic still rejects the token, the integration logs in again with the stored password. You're only asked to sign in again if Panasonic demands a 2FA code.
- **"New version app has been published" (error 4106).** The current app version is detected automatically and re-detected when Panasonic rejects it. There is also a manual override in the options.
- **Updated terms & policies (error 4103).** Setup offers to accept them. During normal operation you get a clear repair notice instead of a cryptic error. Automatic acceptance is opt-in.
- **Two-factor authentication.** Both of Panasonic's 2FA variants (classic and the newer Guardian page) are supported in the setup and re-authentication flows.
- **Unit offline / slow.** If live status fails, the integration falls back to Panasonic's cached status and backs off the live endpoint for a while.

## Installation

### Via HACS (custom repository)

1. Put this folder in a GitHub repository of your own (see [Publishing](#publishing-your-copy)).
2. In Home Assistant: **HACS → ⋮ → Custom repositories**. Add your repository URL with type **Integration**.
3. Find **Panasonic Comfort Cloud (AC)** in HACS, click **Download**, then restart Home Assistant.

### Manual

Copy `custom_components/panasonic_ac` into `<config>/custom_components/`, then restart Home Assistant.

## Setup

1. **Settings → Devices & services → Add integration → Panasonic Comfort Cloud (AC).**
2. Enter your Panasonic ID (email) and password.
3. If Panasonic asks for one, enter the 2FA code (SMS, email or authenticator app).
4. If Panasonic has new terms pending, you can accept them here or cancel and accept them in the Comfort Cloud app.

**Recommended:** create a second Panasonic ID just for Home Assistant and share your air conditioners with it from the app. The phone app and Home Assistant then never invalidate each other's sessions.

### Options

| Option | Default | Notes |
|---|---|---|
| Polling interval | 120 s | 30–900 s. Commands update the UI immediately and re-poll ~8 s later, so a long interval is fine. |
| Daily energy sensors | On | Read every 15 minutes. |
| Accept updated Panasonic terms automatically | Off | Turned on if you accepted terms during setup. |
| Comfort Cloud app version override | empty | Only set this if the logs say the app version was rejected. |

## Before installing: check your login

`scripts/check_login.py` uses the same API client to log in and list your units with their current state. You can run it on any computer:

```bash
pip install aiohttp
python scripts/check_login.py          # add --debug to see every HTTP step
```

If this prints your air conditioners, the integration will work too.

## Troubleshooting

- **Debug logs:** add this to `configuration.yaml`, then restart:
  ```yaml
  logger:
    logs:
      custom_components.panasonic_ac: debug
  ```
- **"Panasonic rejected app version … as outdated":** open the Comfort Cloud app's settings (or its app store page), copy the version number, and set it under the integration options.
- **Repair notice "Panasonic terms need to be accepted":** open the Comfort Cloud app with the same account and accept them, or enable automatic acceptance in the options.
- **Asked to sign in again repeatedly:** use a dedicated Panasonic ID for Home Assistant (see Setup).

## Migrating from `panasonic_cc`

The two integrations use different domains, so they can be installed side by side. Remove the old one when you're happy. Entity IDs are derived from the device names in the Comfort Cloud app (for example `climate.living_room`), so update any dashboards or automations that referenced the old entity IDs.

## Privacy

Your Panasonic ID, password and session tokens are stored in Home Assistant's config entry storage, like every other cloud integration. The integration only talks to Panasonic's servers (`authglb.digital.panasonic.com`, `accsmart.panasonic.com`) and to the Google Play Store page of the Comfort Cloud app to read its current version number.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

The tests run the real integration inside Home Assistant against `tests/fake_panasonic.py`, a local imitation of Panasonic's Auth0 and Comfort Cloud servers. It covers login, both 2FA variants, token rotation and revocation, the app-version and terms gates, status, control and energy.

## Publishing your copy

1. Create a GitHub repository (for example `ha-panasonic-ac`) and push this folder to it.
2. Replace `YOUR_GITHUB_USER` in `custom_components/panasonic_ac/manifest.json` with your GitHub username.
3. Optional: create a GitHub release (for example `v1.0.0`) so HACS shows versions instead of commit hashes.

The included GitHub workflow runs Home Assistant's `hassfest` check, the HACS validator and the test suite on every push.

## Credits

The protocol knowledge comes from the community's reverse-engineering work in [sockless-coding/panasonic_cc](https://github.com/sockless-coding/panasonic_cc) and [aio-panasonic-comfort-cloud](https://github.com/sockless-coding/aio-panasonic-comfort-cloud) (MIT). This project is not affiliated with Panasonic.

## License

MIT
