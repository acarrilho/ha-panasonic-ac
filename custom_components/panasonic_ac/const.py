"""Constants for the Panasonic Comfort Cloud (AC) integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "panasonic_ac"
MANUFACTURER: Final = "Panasonic"

CONF_SESSION: Final = "session"
CONF_APP_VERSION: Final = "app_version"
CONF_AUTO_ACCEPT_AGREEMENTS: Final = "auto_accept_agreements"
CONF_ENABLE_ENERGY: Final = "enable_energy"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL: Final = 120
MIN_SCAN_INTERVAL: Final = 30
MAX_SCAN_INTERVAL: Final = 900
DEFAULT_ENABLE_ENERGY: Final = True

ENERGY_INTERVAL: Final = timedelta(minutes=15)
# Delay before re-reading state after a command, so the cloud has caught up
REFRESH_AFTER_COMMAND: Final = 8

ISSUE_AGREEMENT_REQUIRED: Final = "agreement_required"
