"""Button platform for Netlea Aquarium."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetleaConfigEntry
from .const import (
    ACTION_REFRESH_STATE,
    ACTION_RESUME_SCHEDULE,
    ACTION_SYNC_TIME,
    ACTION_TEMP_OFF,
    ACTION_TEMP_ON,
)
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity


@dataclass(frozen=True, kw_only=True)
class NetleaButtonEntityDescription(ButtonEntityDescription):
    """Declarative Netlea action button description."""

    press_fn: Callable[[NetleaCoordinator], Awaitable[Any]]


BUTTONS: tuple[NetleaButtonEntityDescription, ...] = (
    NetleaButtonEntityDescription(
        key=ACTION_TEMP_ON,
        translation_key="temp_on",
        icon="mdi:lightbulb-on-outline",
        press_fn=lambda c: c.async_temp_on(),
    ),
    NetleaButtonEntityDescription(
        key=ACTION_TEMP_OFF,
        translation_key="temp_off",
        icon="mdi:lightbulb-off-outline",
        press_fn=lambda c: c.async_temp_off(),
    ),
    NetleaButtonEntityDescription(
        key=ACTION_RESUME_SCHEDULE,
        translation_key="resume_schedule",
        icon="mdi:calendar-sync-outline",
        press_fn=lambda c: c.async_resume_schedule(),
    ),
    NetleaButtonEntityDescription(
        key=ACTION_REFRESH_STATE,
        translation_key="refresh_state",
        icon="mdi:refresh",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda c: c.async_refresh_state(include_schedule=True),
    ),
    NetleaButtonEntityDescription(
        key=ACTION_SYNC_TIME,
        translation_key="sync_time",
        icon="mdi:clock-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda c: c.async_sync_time(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Netlea action buttons."""
    async_add_entities(NetleaActionButton(entry.runtime_data, desc) for desc in BUTTONS)


class NetleaActionButton(NetleaEntity, ButtonEntity):
    """Netlea aquarium control button."""

    entity_description: NetleaButtonEntityDescription

    def __init__(self, coordinator: NetleaCoordinator, description: NetleaButtonEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        await self.entity_description.press_fn(self.coordinator)
