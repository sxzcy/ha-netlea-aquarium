"""Light platform for Netlea Aquarium."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import current_trip_name, is_main_light_on, mode_summary
from .const import DOMAIN
from .coordinator import NetleaDataUpdateCoordinator
from .entity import NetleaBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Netlea light entities."""
    coordinator: NetleaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([NetleaAquariumLight(coordinator)])


class NetleaAquariumLight(NetleaBaseEntity, LightEntity):
    """Netlea aquarium light group."""

    _attr_name = None
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF
    _attr_translation_key = "aquarium_light"

    def __init__(self, coordinator: NetleaDataUpdateCoordinator) -> None:
        """Initialize the light."""
        super().__init__(coordinator, "light")

    @property
    def is_on(self) -> bool | None:
        """Return whether the light is on."""
        return is_main_light_on(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        last_control = self.device.get("_last_control") or {}
        return {
            "mode": mode_summary(self.device),
            "current_trip": current_trip_name(self.device),
            "control_real_address": self.device.get("controlRealAddress"),
            "replied_addresses": last_control.get("replied_addresses"),
            "last_reply_count": last_control.get("reply_count"),
            "last_command": last_control.get("action"),
            "last_frame": last_control.get("frame"),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on by forcing temporary full viewing light."""
        await self.coordinator.async_temp_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off temporarily."""
        await self.coordinator.async_temp_off()
