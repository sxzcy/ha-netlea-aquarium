"""Fan platform for Netlea Aquarium cooling fan (0..10 gears mapped to hardware PWM)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetleaConfigEntry, presets, protocol as proto
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Netlea cooling fan entity."""
    async_add_entities([NetleaCoolingFan(entry.runtime_data)])


class NetleaCoolingFan(NetleaEntity, FanEntity):
    """Cooling fan entity with 10 discrete speed gears and >20W thermal safety floor."""

    _attr_icon = "mdi:fan"
    _attr_translation_key = "cooling_fan"
    _attr_speed_count = 10
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "cooling_fan")

    @property
    def _gear(self) -> int:
        _, _, _, _, f_pwm = proto.live_master_channels(self.device)
        return presets.fan_pwm_to_gear(f_pwm)

    @property
    def is_on(self) -> bool:
        return self._gear > 0

    @property
    def percentage(self) -> int:
        return self._gear * 10

    async def async_set_percentage(self, percentage: int) -> None:
        gear = max(0, min(10, round(int(percentage) / 10)))
        await self.coordinator.async_set_fan_gear(gear)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        gear = max(1, min(10, round(int(percentage) / 10))) if percentage is not None else 3
        await self.coordinator.async_set_fan_gear(gear)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_fan_gear(0)
