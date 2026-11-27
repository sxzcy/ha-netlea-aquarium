"""Number platform for Netlea Aquarium (temp duration, preset grade, RGBW channel sliders)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetleaConfigEntry, const, presets, protocol as proto
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity

_CHANNELS: tuple[tuple[str, str, str, str], ...] = (
    ("channel_r", "R", "channel_r", "mdi:led-on"),
    ("channel_w", "W", "channel_w", "mdi:white-balance-sunny"),
    ("channel_g", "G", "channel_g", "mdi:leaf"),
    ("channel_b", "B", "channel_b", "mdi:water"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Netlea number entities."""
    coordinator = entry.runtime_data
    entities: list[NumberEntity] = [
        NetleaTempDurationNumber(coordinator),
        NetleaPresetGradeNumber(coordinator),
        *(NetleaChannelNumber(coordinator, key, ch, tkey, icon) for key, ch, tkey, icon in _CHANNELS),
    ]
    async_add_entities(entities)


class NetleaTempDurationNumber(NetleaEntity, NumberEntity):
    """Temporary viewing light duration in minutes (1..300 min, default 60)."""

    _attr_icon = "mdi:timer-outline"
    _attr_translation_key = "temp_duration"
    _attr_native_min_value = float(const.MIN_TEMP_MINUTES)
    _attr_native_max_value = float(const.MAX_TEMP_MINUTES)
    _attr_native_step = 5.0
    _attr_native_unit_of_measurement = "min"
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "temp_duration")

    @property
    def native_value(self) -> float:
        return float(self.coordinator.temp_on_minutes)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.temp_on_minutes = proto.clamp_int(
            value, const.DEFAULT_TEMP_ON_MINUTES, const.MIN_TEMP_MINUTES, const.MAX_TEMP_MINUTES
        )
        if self.coordinator.data is not None:
            self.coordinator.async_set_updated_data(dict(self.coordinator.data))


class NetleaPresetGradeNumber(NetleaEntity, NumberEntity):
    """Brightness grade (1..20) for the active official spectrum preset."""

    _attr_icon = "mdi:brightness-6"
    _attr_translation_key = "preset_grade"
    _attr_native_min_value = 1.0
    _attr_native_max_value = 20.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "preset_grade")

    @property
    def native_value(self) -> float:
        return float(self.coordinator.active_preset_grade)

    async def async_set_native_value(self, value: float) -> None:
        grade = max(1, min(20, int(round(value))))
        await self.coordinator.async_apply_preset(self.coordinator.active_preset_name, grade=grade)


class NetleaChannelNumber(NetleaEntity, NumberEntity):
    """Individual R/W/G/B channel percentage slider (0..100%, mapped via cSpectrums + 91W cap)."""

    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self, coordinator: NetleaCoordinator, key: str, channel: str, translation_key: str, icon: str
    ) -> None:
        super().__init__(coordinator, key)
        self._channel = channel
        self._attr_translation_key = translation_key
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        r, w, g, b, _ = proto.live_master_channels(self.device)
        idx_map = {"R": r, "W": w, "G": g, "B": b}
        return presets.channel_pwm_to_percent(self._channel, idx_map[self._channel])

    async def async_set_native_value(self, value: float) -> None:
        r, w, g, b, f_pwm = proto.live_master_channels(self.device)
        ch_vals = {"R": r, "W": w, "G": g, "B": b}
        ch_vals[self._channel] = presets.percent_to_channel_pwm(self._channel, value)
        await self.coordinator.async_set_main_channels(
            ch_vals["R"], ch_vals["W"], ch_vals["G"], ch_vals["B"], f_pwm or None
        )
