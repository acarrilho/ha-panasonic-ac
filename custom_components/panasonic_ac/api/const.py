"""Protocol constants for the Panasonic Comfort Cloud API (no Home Assistant imports)."""

from __future__ import annotations

from enum import IntEnum

# --- Auth0 / Panasonic ID -------------------------------------------------
AUTH_BASE = "https://authglb.digital.panasonic.com"
APP_CLIENT_ID = "Xmy6xIYIitMxngjB2rHvlm6HSDNnaMJx"
AUTH0_CLIENT = (
    "eyJuYW1lIjoiQXV0aDAuQW5kcm9pZCIsImVudiI6eyJhbmRyb2lkIjoiMzAifSwidmVyc2lvbiI6IjIuOS4zIn0="
)
REDIRECT_URI = (
    "panasonic-iot-cfc://authglb.digital.panasonic.com/android/com.panasonic.ACCsmart/callback"
)
SCOPE = "openid offline_access comfortcloud.control a2w.control"
AUDIENCE = f"https://digital.panasonic.com/{APP_CLIENT_ID}/api/v1/"
AUTH_TENANT = "pdpauthglb-a1"
AUTH_CONNECTION = "PanasonicID-Authentication"
AUTH_API_USER_AGENT = "okhttp/4.10.0"
AUTH_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/113.0.0.0 Mobile Safari/537.36"
)

# --- Comfort Cloud API ----------------------------------------------------
API_BASE = "https://accsmart.panasonic.com"
API_SIGNING_KEY = "521325fb2dd486bf4831b47644317fca"
API_USER_AGENT = "G-RAC"
APP_NAME = "Comfort Cloud"

# The API rejects requests whose X-APP-VERSION is older than the current app
# (error 4106). 4.4.0 was accepted as of late September 2026; the client tries
# to detect a newer one from the Play Store, and users can override it.
APP_VERSION_FALLBACK = "4.4.0"
PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.panasonic.ACCsmart&hl=en"

# Error codes embedded in Comfort Cloud error responses
ERROR_APP_VERSION_OUTDATED = "4106"
ERROR_AGREEMENT_REQUIRED = "4103"
ERROR_TOKEN_EXPIRED = "4100"

# Values the device reports when a temperature isn't available
INVALID_TEMPERATURES = (126, -255, 255)

# Device types that are not air conditioners (handled by other integrations)
AQUAREA_DEVICE_TYPE = "2"
HWS_DEVICE_TYPE = "11"
# Ducted / commercial units: reduced control set in the official app
CAC_DEVICE_TYPES = ("4", "5", "6", "8", "9", "10")

# Agreement types the official app accepts (3 = Turkey-only service agreement)
AGREEMENT_TYPES_TO_ACCEPT = (1, 2, 4)


class Power(IntEnum):
    OFF = 0
    ON = 1


class OperationMode(IntEnum):
    AUTO = 0
    DRY = 1
    COOL = 2
    HEAT = 3
    FAN = 4


class FanSpeed(IntEnum):
    AUTO = 0
    LOW = 1
    LOW_MID = 2
    MID = 3
    HIGH_MID = 4
    HIGH = 5


class EcoMode(IntEnum):
    """Called ecoMode by the API; it's really Normal/Powerful/Quiet."""

    NORMAL = 0
    POWERFUL = 1
    QUIET = 2


class AirSwingUD(IntEnum):
    """Vertical louver positions. AUTO is expressed via fanAutoMode."""

    AUTO = -1
    UP = 0
    DOWN = 1
    MID = 2
    UP_MID = 3
    DOWN_MID = 4
    SWING = 5


class AirSwingLR(IntEnum):
    """Horizontal louver positions. AUTO is expressed via fanAutoMode."""

    AUTO = -1
    RIGHT = 0
    LEFT = 1
    MID = 2
    RIGHT_MID = 4
    LEFT_MID = 5
    UNAVAILABLE = 6


class FanAutoMode(IntEnum):
    BOTH = 0
    DISABLED = 1
    UD = 2
    LR = 3


class OnOffFeature(IntEnum):
    """nanoe / ecoNavi / ecoFunctionData / iAuto use 0=unavailable, 1=off, 2=on."""

    UNAVAILABLE = 0
    OFF = 1
    ON = 2


class StatusSource(IntEnum):
    LIVE = 0
    CACHED = 1
