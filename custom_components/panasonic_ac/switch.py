"""Feature switches (nanoe, ECONAVI, AI ECO, inside cleaning)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AcDevice, OnOffFeature
from .coordinator import PanasonicConfigEntry, PanasonicCoordinator
from .entity import PanasonicEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class PanasonicSwitchDescription(SwitchEntityDescription):
    exists_fn: Callable[[AcDevice], bool]
    is_on_fn: Callable[[AcDevice], bool]
    parameter: str
    on_value: int
    off_value: int


SWITCHES: tuple[PanasonicSwitchDescription, ...] = (
    PanasonicSwitchDescription(
        key="nanoe",
        translation_key="nanoe",
        exists_fn=lambda d: d.has_nanoe,
        is_on_fn=lambda d: d.nanoe == OnOffFeature.ON,
        parameter="nanoe",
        on_value=OnOffFeature.ON.value,
        off_value=OnOffFeature.OFF.value,
    ),
    PanasonicSwitchDescription(
        key="eco_navi",
        translation_key="eco_navi",
        exists_fn=lambda d: d.has_eco_navi,
        is_on_fn=lambda d: d.eco_navi == OnOffFeature.ON,
        parameter="ecoNavi",
        on_value=OnOffFeature.ON.value,
        off_value=OnOffFeature.OFF.value,
    ),
    PanasonicSwitchDescription(
        key="ai_eco",
        translation_key="ai_eco",
        exists_fn=lambda d: d.has_eco_function,
        is_on_fn=lambda d: d.eco_function == OnOffFeature.ON,
        parameter="ecoFunctionData",
        on_value=OnOffFeature.ON.value,
        off_value=OnOffFeature.OFF.value,
    ),
    PanasonicSwitchDescription(
        key="inside_cleaning",
        translation_key="inside_cleaning",
        entity_category=EntityCategory.CONFIG,
        exists_fn=lambda d: d.has_inside_cleaning,
        is_on_fn=lambda d: d.inside_cleaning,
        parameter="insideCleaning",
        on_value=1,
        off_value=0,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PanasonicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        PanasonicSwitch(coordinator, device, description)
        for device in coordinator.data.values()
        for description in SWITCHES
        if description.exists_fn(device)
    )


class PanasonicSwitch(PanasonicEntity, SwitchEntity):
    entity_description: PanasonicSwitchDescription

    def __init__(
        self, coordinator: PanasonicCoordinator, device: AcDevice, description: PanasonicSwitchDescription
    ) -> None:
        super().__init__(coordinator, device, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.is_on_fn(self.device)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send({self.entity_description.parameter: self.entity_description.on_value})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send({self.entity_description.parameter: self.entity_description.off_value})
