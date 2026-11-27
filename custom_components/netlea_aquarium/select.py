"""Select platform for Netlea Aquarium (control mode, spectrum presets, atmosphere follow)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetleaConfigEntry, const, presets, protocol as proto
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Netlea select entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NetleaControlModeSelect(coordinator),
            NetleaSpectrumPresetSelect(coordinator),
            NetleaAtmosphereFollowSelect(coordinator),
        ]
    )


class NetleaControlModeSelect(NetleaEntity, SelectEntity):
    """Option C control mode selector: schedule_auto (0x1A temp) vs manual_hold (0x10 persistent)."""

    _attr_icon = "mdi:tune-variant"
    _attr_translation_key = "control_mode"
    _attr_options = list(const.VALID_CONTROL_MODES)

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "control_mode")

    @property
    def current_option(self) -> str:
        return self.coordinator.control_mode

    async def async_select_option(self, option: str) -> None:
        if option not in const.VALID_CONTROL_MODES:
            return
        self.coordinator.control_mode = option
        if self.coordinator.data is not None:
            self.coordinator.async_set_updated_data(dict(self.coordinator.data))


class NetleaSpectrumPresetSelect(NetleaEntity, SelectEntity):
    """Official spectrum preset selector (12 presets for N7-90 / 7S-90)."""

    _attr_icon = "mdi:palette-swatch-outline"
    _attr_translation_key = "spectrum_preset"

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "spectrum_preset")
        self._attr_options = presets.preset_names_for_model(coordinator.model_name)

    @property
    def current_option(self) -> str | None:
        r, w, g, b, _ = proto.live_master_channels(self.device)
        matched = presets.match_preset_from_channels(self.coordinator.model_name, r, w, g, b)
        if matched:
            return matched[0]
        return self.coordinator.active_preset_name

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_apply_preset(option)


class NetleaAtmosphereFollowSelect(NetleaEntity, SelectEntity):
    """Atmosphere follow mode selector (0x22: manual / follow_main_on / follow_main_off)."""

    _attr_icon = "mdi:link-variant"
    _attr_translation_key = "atmosphere_follow"
    _attr_options = list(const.ATMOS_FOLLOW_MODES)

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "atmosphere_follow")

    @property
    def current_option(self) -> str:
        atmos = proto.live_atmosphere_state(self.device)
        return const.CODE_TO_ATMOS_FOLLOW.get(atmos.get("followType", 0), const.ATMOS_FOLLOW_MANUAL)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_atmosphere_follow(option)
