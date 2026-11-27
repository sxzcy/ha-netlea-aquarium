"""Switch platform for Netlea Aquarium (offline voice toggle 0x17)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetleaConfigEntry
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Netlea switch entities."""
    async_add_entities([NetleaOfflineVoiceSwitch(entry.runtime_data)])


class NetleaOfflineVoiceSwitch(NetleaEntity, SwitchEntity):
    """Offline voice control switch (0x17)."""

    _attr_icon = "mdi:microphone-outline"
    _attr_translation_key = "offline_voice"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "offline_voice")

    @property
    def is_on(self) -> bool:
        return self.coordinator.offline_voice_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_offline_voice(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_offline_voice(False)
