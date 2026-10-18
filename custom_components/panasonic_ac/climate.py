"""Climate entity for Panasonic Comfort Cloud air conditioners."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    PRESET_BOOST,
    PRESET_NONE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AcDevice, AirSwingLR, AirSwingUD, EcoMode, FanAutoMode, FanSpeed, OperationMode, Power
from .coordinator import PanasonicConfigEntry, PanasonicCoordinator
from .entity import PanasonicEntity

PARALLEL_UPDATES = 1

HVAC_MODES: dict[OperationMode, HVACMode] = {
    OperationMode.AUTO: HVACMode.HEAT_COOL,
    OperationMode.COOL: HVACMode.COOL,
    OperationMode.HEAT: HVACMode.HEAT,
    OperationMode.DRY: HVACMode.DRY,
    OperationMode.FAN: HVACMode.FAN_ONLY,
}
HVAC_MODES_REVERSE = {v: k for k, v in HVAC_MODES.items()}

FAN_MODES: dict[FanSpeed, str] = {
    FanSpeed.AUTO: "auto",
    FanSpeed.LOW: "low",
    FanSpeed.LOW_MID: "medium_low",
    FanSpeed.MID: "medium",
    FanSpeed.HIGH_MID: "medium_high",
    FanSpeed.HIGH: "high",
}
FAN_MODES_REVERSE = {v: k for k, v in FAN_MODES.items()}

PRESET_QUIET = "quiet"
PRESETS: dict[EcoMode, str] = {
    EcoMode.NORMAL: PRESET_NONE,
    EcoMode.POWERFUL: PRESET_BOOST,
    EcoMode.QUIET: PRESET_QUIET,
}
PRESETS_REVERSE = {v: k for k, v in PRESETS.items()}

SWING_VERTICAL: dict[AirSwingUD, str] = {
    AirSwingUD.AUTO: "auto",
    AirSwingUD.UP: "up",
    AirSwingUD.UP_MID: "mid_up",
    AirSwingUD.MID: "mid",
    AirSwingUD.DOWN_MID: "mid_down",
    AirSwingUD.DOWN: "down",
}
SWING_VERTICAL_REVERSE = {v: k for k, v in SWING_VERTICAL.items()}

SWING_HORIZONTAL: dict[AirSwingLR, str] = {
    AirSwingLR.AUTO: "auto",
    AirSwingLR.LEFT: "left",
    AirSwingLR.LEFT_MID: "mid_left",
    AirSwingLR.MID: "mid",
    AirSwingLR.RIGHT_MID: "mid_right",
    AirSwingLR.RIGHT: "right",
}
SWING_HORIZONTAL_REVERSE = {v: k for k, v in SWING_HORIZONTAL.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(PanasonicClimate(coordinator, device) for device in coordinator.data.values())


def fan_auto_mode(vertical_auto: bool, horizontal_auto: bool) -> FanAutoMode:
    if vertical_auto and horizontal_auto:
        return FanAutoMode.BOTH
    if vertical_auto:
        return FanAutoMode.UD
    if horizontal_auto:
        return FanAutoMode.LR
    return FanAutoMode.DISABLED


class PanasonicClimate(PanasonicEntity, ClimateEntity):
    """An air conditioner."""

    _attr_name = None
    _attr_translation_key = "air_conditioner"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_precision = PRECISION_HALVES

    def __init__(self, coordinator: PanasonicCoordinator, device: AcDevice) -> None:
        super().__init__(coordinator, device, "climate")
        self._attr_hvac_modes = [HVACMode.OFF] + [HVAC_MODES[m] for m in device.supported_modes]

        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if device.supported_fan_speeds:
            features |= ClimateEntityFeature.FAN_MODE
        if len(device.supported_eco_modes) > 1:
            features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = [PRESETS[m] for m in device.supported_eco_modes]
        if device.has_vertical_swing:
            features |= ClimateEntityFeature.SWING_MODE
            self._attr_swing_modes = list(SWING_VERTICAL.values())
        if device.has_horizontal_swing:
            features |= ClimateEntityFeature.SWING_HORIZONTAL_MODE
            self._attr_swing_horizontal_modes = list(SWING_HORIZONTAL.values())
        self._attr_supported_features = features

    # ---------------------------------------------------------------- state
    @property
    def hvac_mode(self) -> HVACMode | None:
        if self.device.power == Power.OFF:
            return HVACMode.OFF
        mode = self.device.mode
        return HVAC_MODES.get(mode) if mode is not None else None

    @property
    def current_temperature(self) -> float | None:
        return self.device.inside_temperature

    @property
    def target_temperature(self) -> float | None:
        return self.device.target_temperature

    @property
    def min_temp(self) -> float:
        return self.device.min_temperature

    @property
    def max_temp(self) -> float:
        return self.device.max_temperature

    @property
    def fan_modes(self) -> list[str]:
        # Can change with the operating mode on ducted units
        return [FAN_MODES[s] for s in self.device.supported_fan_speeds]

    @property
    def fan_mode(self) -> str | None:
        speed = self.device.fan_speed
        return FAN_MODES.get(speed) if speed is not None else None

    @property
    def preset_mode(self) -> str | None:
        return PRESETS.get(self.device.eco_mode, PRESET_NONE)

    @property
    def swing_mode(self) -> str | None:
        value = self.device.swing_ud
        return SWING_VERTICAL.get(value) if value is not None else None

    @property
    def swing_horizontal_mode(self) -> str | None:
        value = self.device.swing_lr
        return SWING_HORIZONTAL.get(value) if value is not None else None

    # ------------------------------------------------------------- commands
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode == HVACMode.OFF:
            await self._send({"operate": Power.OFF.value})
            return
        await self._send(
            {"operate": Power.ON.value, "operationMode": HVAC_MODES_REVERSE[hvac_mode].value}
        )

    async def async_turn_on(self) -> None:
        await self._send({"operate": Power.ON.value})

    async def async_turn_off(self) -> None:
        await self._send({"operate": Power.OFF.value})

    async def async_set_temperature(self, **kwargs: Any) -> None:
        parameters: dict[str, Any] = {}
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            parameters["temperatureSet"] = round(float(temperature) * 2) / 2
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            if hvac_mode == HVACMode.OFF:
                parameters["operate"] = Power.OFF.value
            else:
                parameters["operate"] = Power.ON.value
                parameters["operationMode"] = HVAC_MODES_REVERSE[hvac_mode].value
        if parameters:
            await self._send(parameters)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self._send({"fanSpeed": FAN_MODES_REVERSE[fan_mode].value})

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self._send({"ecoMode": PRESETS_REVERSE[preset_mode].value})

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        position = SWING_VERTICAL_REVERSE[swing_mode]
        horizontal_auto = self.device.has_horizontal_swing and self.device.swing_lr == AirSwingLR.AUTO
        parameters: dict[str, Any] = {
            "fanAutoMode": fan_auto_mode(position == AirSwingUD.AUTO, horizontal_auto).value
        }
        if position != AirSwingUD.AUTO:
            parameters["airSwingUD"] = position.value
        await self._send(parameters)

    async def async_set_swing_horizontal_mode(self, swing_horizontal_mode: str) -> None:
        position = SWING_HORIZONTAL_REVERSE[swing_horizontal_mode]
        vertical_auto = self.device.swing_ud == AirSwingUD.AUTO
        parameters: dict[str, Any] = {
            "fanAutoMode": fan_auto_mode(vertical_auto, position == AirSwingLR.AUTO).value
        }
        if position != AirSwingLR.AUTO:
            parameters["airSwingLR"] = position.value
        await self._send(parameters)
