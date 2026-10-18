"""Data model for a Comfort Cloud air conditioner.

The device state is kept as the raw dicts the API returns (``features`` is the
top level of the status response, ``params`` its ``parameters`` object), and
everything else is derived from them. That makes optimistic updates trivial:
merge the parameters that were just sent into ``params``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .const import (
    AQUAREA_DEVICE_TYPE,
    CAC_DEVICE_TYPES,
    HWS_DEVICE_TYPE,
    INVALID_TEMPERATURES,
    AirSwingLR,
    AirSwingUD,
    EcoMode,
    FanAutoMode,
    FanSpeed,
    OnOffFeature,
    OperationMode,
    Power,
    StatusSource,
)

MIN_TEMP = 16.0
MIN_TEMP_SUMMER_HOUSE = 8.0
MAX_TEMP = 30.0


def _enum(enum_cls, value, default=None):
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return default


def _temperature(value) -> float | None:
    if value is None or not isinstance(value, (int, float)):
        return None
    if value in INVALID_TEMPERATURES:
        return None
    return float(value)


@dataclass
class AcDevice:
    """One air conditioner (indoor unit) on the account."""

    guid: str
    name: str
    group: str
    model: str
    device_type: str | None
    features: dict[str, Any] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    status_source: StatusSource = StatusSource.LIVE
    timestamp: datetime | None = None
    energy_today: float | None = None
    available: bool = True

    # ------------------------------------------------------------------ parsing
    @staticmethod
    def is_air_conditioner(entry: dict[str, Any]) -> bool:
        """True if a /device/group entry is an air conditioner we can control."""
        if not entry or not entry.get("deviceGuid"):
            return False
        if str(entry.get("deviceType")) in (AQUAREA_DEVICE_TYPE, HWS_DEVICE_TYPE):
            return False
        return "parameters" in entry

    @classmethod
    def from_group_entry(cls, entry: dict[str, Any], group_name: str) -> AcDevice:
        return cls(
            guid=entry["deviceGuid"],
            name=entry.get("deviceName") or entry["deviceGuid"],
            group=group_name,
            model=entry.get("deviceModuleNumber") or "",
            device_type=str(entry["deviceType"]) if entry.get("deviceType") is not None else None,
            params=dict(entry.get("parameters") or {}),
        )

    def load_status(self, status: dict[str, Any], source: StatusSource) -> None:
        """Load a /deviceStatus response."""
        self.features = {k: v for k, v in status.items() if k != "parameters"}
        if isinstance(status.get("parameters"), dict):
            self.params = dict(status["parameters"])
        self.status_source = source
        if isinstance(status.get("timestamp"), (int, float)):
            try:
                self.timestamp = datetime.fromtimestamp(status["timestamp"] / 1000).astimezone()
            except (OverflowError, OSError, ValueError):
                self.timestamp = None
        self.available = True

    def apply(self, changes: dict[str, Any]) -> None:
        """Optimistically merge parameters that were just sent to the device."""
        self.params.update(changes)

    # -------------------------------------------------------------- state
    @property
    def is_cac(self) -> bool:
        return self.device_type in CAC_DEVICE_TYPES

    @property
    def power(self) -> Power:
        return _enum(Power, self.params.get("operate"), Power.OFF)

    @property
    def mode(self) -> OperationMode | None:
        return _enum(OperationMode, self.params.get("operationMode"))

    @property
    def target_temperature(self) -> float | None:
        return _temperature(self.params.get("temperatureSet"))

    @property
    def inside_temperature(self) -> float | None:
        return _temperature(self.params.get("insideTemperature"))

    @property
    def outside_temperature(self) -> float | None:
        return _temperature(self.params.get("outTemperature"))

    @property
    def fan_speed(self) -> FanSpeed | None:
        return _enum(FanSpeed, self.params.get("fanSpeed"))

    @property
    def eco_mode(self) -> EcoMode:
        return _enum(EcoMode, self.params.get("ecoMode"), EcoMode.NORMAL)

    @property
    def fan_auto_mode(self) -> FanAutoMode:
        return _enum(FanAutoMode, self.params.get("fanAutoMode"), FanAutoMode.DISABLED)

    @property
    def swing_ud(self) -> AirSwingUD | None:
        if self.fan_auto_mode in (FanAutoMode.BOTH, FanAutoMode.UD):
            return AirSwingUD.AUTO
        return _enum(AirSwingUD, self.params.get("airSwingUD"))

    @property
    def swing_lr(self) -> AirSwingLR | None:
        if self.fan_auto_mode in (FanAutoMode.BOTH, FanAutoMode.LR):
            return AirSwingLR.AUTO
        return _enum(AirSwingLR, self.params.get("airSwingLR"))

    def _on_off(self, key: str) -> OnOffFeature:
        return _enum(OnOffFeature, self.params.get(key), OnOffFeature.UNAVAILABLE)

    @property
    def nanoe(self) -> OnOffFeature:
        # 3 (mode G) and 4 (all) both mean "on" for a simple switch
        value = self.params.get("nanoe")
        if value in (3, 4):
            return OnOffFeature.ON
        return self._on_off("nanoe")

    @property
    def eco_navi(self) -> OnOffFeature:
        return self._on_off("ecoNavi")

    @property
    def eco_function(self) -> OnOffFeature:
        return self._on_off("ecoFunctionData")

    @property
    def inside_cleaning(self) -> bool:
        return self.params.get("insideCleaning") == 1

    # -------------------------------------------------------- capabilities
    def _feature(self, key: str, default: bool = False) -> bool:
        return bool(self.features.get(key, default))

    @property
    def supported_modes(self) -> list[OperationMode]:
        modes = []
        if self._feature("autoMode", True):
            modes.append(OperationMode.AUTO)
        if self._feature("coolMode", True):
            modes.append(OperationMode.COOL)
        if self._feature("heatMode", True):
            modes.append(OperationMode.HEAT)
        if self._feature("dryMode", True):
            modes.append(OperationMode.DRY)
        if self._feature("fanMode", True):
            modes.append(OperationMode.FAN)
        return modes

    @property
    def supported_fan_speeds(self) -> list[FanSpeed]:
        """Mirrors the official app: CAC units follow fanSpeedMode (0, 3 or 5 steps)."""
        speeds = [FanSpeed.LOW, FanSpeed.LOW_MID, FanSpeed.MID, FanSpeed.HIGH_MID, FanSpeed.HIGH]
        auto = True
        if self.is_cac:
            step_mode = self.features.get("fanSpeedMode")
            if step_mode == 0:
                return []
            if step_mode == 3:
                speeds = [FanSpeed.LOW, FanSpeed.MID, FanSpeed.HIGH]
            auto = self.mode != OperationMode.FAN
        return ([FanSpeed.AUTO] if auto else []) + speeds

    @property
    def supported_eco_modes(self) -> list[EcoMode]:
        modes = [EcoMode.NORMAL]
        if not self.is_cac and self._feature("powerfulMode"):
            modes.append(EcoMode.POWERFUL)
        if not self.is_cac and self._feature("quietMode"):
            modes.append(EcoMode.QUIET)
        return modes

    @property
    def has_vertical_swing(self) -> bool:
        return "airSwingUD" in self.params or "fanAutoMode" in self.params

    @property
    def has_horizontal_swing(self) -> bool:
        if not self._feature("airSwingLR") or self.swing_lr == AirSwingLR.UNAVAILABLE:
            return False
        if self.device_type == "7":
            return True
        if self.is_cac:
            return False
        # airDirection 1/3 means the left/right louver is disabled on this unit
        return self.params.get("airDirection") not in (1, 3)

    @property
    def has_nanoe(self) -> bool:
        return (
            not self.is_cac
            and self._feature("nanoe")
            and self.nanoe != OnOffFeature.UNAVAILABLE
        )

    @property
    def has_eco_navi(self) -> bool:
        return self._feature("ecoNavi") and self.eco_navi != OnOffFeature.UNAVAILABLE

    @property
    def has_eco_function(self) -> bool:
        return (
            not self.is_cac
            and bool(self.features.get("ecoFunction", 0))
            and self.eco_function != OnOffFeature.UNAVAILABLE
        )

    @property
    def has_inside_cleaning(self) -> bool:
        return self._feature("insideCleaning")

    @property
    def has_energy(self) -> bool:
        return self.features.get("powerConsumptionFlg", True) is not False

    @property
    def min_temperature(self) -> float:
        if self.mode == OperationMode.HEAT and self.features.get("summerHouse", 0) in (1, 2, 3):
            return MIN_TEMP_SUMMER_HOUSE
        return MIN_TEMP

    @property
    def max_temperature(self) -> float:
        return MAX_TEMP
