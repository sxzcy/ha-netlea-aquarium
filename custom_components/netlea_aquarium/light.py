"""Light platform for Netlea Aquarium."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NetleaConfigEntry
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity
from .protocol import current_trip_name, is_main_light_on, mode_summary


async def async_setup_entry(hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the Netlea main aquarium light."""
    async_add_entities([NetleaAquariumLight(entry.runtime_data)])


class NetleaAquariumLight(NetleaEntity, LightEntity):
    """Netlea aquarium main light group."""

    _attr_icon = "mdi:led-strip-variant"
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF
    _attr_translation_key = "aquarium_light"

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "light")

    @property
    def is_on(self) -> bool | None:
        return is_main_light_on(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.now()
        return {
            "mode": mode_summary(self.device, now),
            "current_trip": current_trip_name(self.device, now),
            "stale": bool(self.device.get("_stale", False)),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_temp_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_temp_off()
