"""Light platform for Netlea Aquarium (main RGBW+Effect light & atmosphere light)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NetleaConfigEntry, const, presets, protocol as proto
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Netlea main aquarium light and atmosphere light."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            NetleaAquariumLight(coordinator),
            NetleaAtmosphereLight(coordinator),
        ]
    )


class NetleaAquariumLight(NetleaEntity, LightEntity):
    """Netlea aquarium main light group (preserves v1.0.0 unique_id `{device_key}_light`)."""

    _attr_icon = "mdi:led-strip-variant"
    _attr_supported_color_modes = {ColorMode.RGBW}
    _attr_color_mode = ColorMode.RGBW
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_translation_key = "aquarium_light"

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "light")

    @property
    def is_on(self) -> bool | None:
        return proto.is_main_light_on(self.device)

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        r, w, g, b, _ = proto.live_master_channels(self.device)
        if r == w == g == b == 0:
            return None
        peak = max(r, w, g, b, 1)
        scale = 255.0 / peak
        # HA RGBW order is (R, G, B, W)
        return (
            min(255, round(r * scale)),
            min(255, round(g * scale)),
            min(255, round(b * scale)),
            min(255, round(w * scale)),
        )

    @property
    def brightness(self) -> int | None:
        if self.is_on is False:
            return 0
        r, w, g, b, _ = proto.live_master_channels(self.device)
        peak = max(r, w, g, b)
        if peak == 0:
            return None
        # Map peak PWM (typical max ~185 on 90W lamps) to 1..255
        return max(1, min(255, round(peak * 255 / 185)))

    @property
    def effect_list(self) -> list[str]:
        return presets.preset_names_for_model(self.coordinator.model_name)

    @property
    def effect(self) -> str | None:
        r, w, g, b, _ = proto.live_master_channels(self.device)
        matched = presets.match_preset_from_channels(self.coordinator.model_name, r, w, g, b)
        return matched[0] if matched else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = dt_util.now()
        r, w, g, b, f_pwm = proto.live_master_channels(self.device)
        return {
            "mode": proto.mode_summary(self.device, now),
            "current_trip": proto.current_trip_name(self.device, now),
            "stale": bool(self.device.get("_stale", False)),
            "control_mode": self.coordinator.control_mode,
            "temp_on_minutes": self.coordinator.temp_on_minutes,
            "power_w": presets.estimate_power_w(r, w, g, b, f_pwm),
            "channels_rwgb": {"R": r, "W": w, "G": g, "B": b},
            "fan_gear": presets.fan_pwm_to_gear(f_pwm),
            "transport": self.coordinator.transport_mode,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        has_effect = ATTR_EFFECT in kwargs and kwargs[ATTR_EFFECT]
        has_rgbw = ATTR_RGBW_COLOR in kwargs and kwargs[ATTR_RGBW_COLOR] is not None
        has_brightness = ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS] is not None

        if not (has_effect or has_rgbw or has_brightness):
            # Option C: plain turn_on preserves v1.0.0 temporary viewing light behavior
            await self.coordinator.async_temp_on()
            return

        if has_effect:
            eff = str(kwargs[ATTR_EFFECT])
            table = presets.presets_for_model(self.coordinator.model_name).get(eff)
            grade = self.coordinator.active_preset_grade
            if table and has_brightness:
                max_g = max(table)
                grade = max(1, min(max_g, round(int(kwargs[ATTR_BRIGHTNESS]) * max_g / 255)))
            await self.coordinator.async_apply_preset(eff, grade=grade)
            return

        cur_r, cur_w, cur_g, cur_b, cur_f = proto.live_master_channels(self.device)
        if has_rgbw:
            ha_r, ha_g, ha_b, ha_w = kwargs[ATTR_RGBW_COLOR]
            bri = int(kwargs.get(ATTR_BRIGHTNESS) or self.brightness or 160)
            scale = (bri / 255.0) * (185.0 / 255.0)
            r = round(int(ha_r) * scale)
            w = round(int(ha_w) * scale)
            g = round(int(ha_g) * scale)
            b = round(int(ha_b) * scale)
        else:
            # Brightness-only adjustment: scale current live channels (or default temp channels)
            bri = max(1, min(255, int(kwargs[ATTR_BRIGHTNESS])))
            if cur_r == cur_w == cur_g == cur_b == 0:
                base_r = self.coordinator.temp_channels["red"]
                base_w = self.coordinator.temp_channels["white"]
                base_g = self.coordinator.temp_channels["green"]
                base_b = self.coordinator.temp_channels["blue"]
            else:
                base_r, base_w, base_g, base_b = cur_r, cur_w, cur_g, cur_b
            peak = max(base_r, base_w, base_g, base_b, 1)
            target_peak = max(1, round(bri * 185 / 255))
            ratio = target_peak / peak
            r = round(base_r * ratio)
            w = round(base_w * ratio)
            g = round(base_g * ratio)
            b = round(base_b * ratio)

        await self.coordinator.async_set_main_channels(r, w, g, b)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_temp_off()


class NetleaAtmosphereLight(NetleaEntity, LightEntity):
    """Independent 5-gear atmosphere light (handles follow-mode exit automatically)."""

    _attr_icon = "mdi:led-outline"
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_translation_key = "atmosphere_light"

    def __init__(self, coordinator: NetleaCoordinator) -> None:
        super().__init__(coordinator, "atmosphere_light")

    @property
    def is_on(self) -> bool | None:
        atmos = proto.live_atmosphere_state(self.device)
        return bool(atmos.get("lampState", 0))

    @property
    def brightness(self) -> int | None:
        atmos = proto.live_atmosphere_state(self.device)
        if not atmos.get("lampState", 0):
            return 0
        gear = presets.atmos_pwm_to_gear(atmos.get("moodLamp", 0))
        return round(gear * 255 / 5)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        atmos = proto.live_atmosphere_state(self.device)
        pwm = atmos.get("moodLamp", 0)
        return {
            "gear": presets.atmos_pwm_to_gear(pwm),
            "pwm": pwm,
            "follow_mode": const.CODE_TO_ATMOS_FOLLOW.get(atmos.get("followType", 0), const.ATMOS_FOLLOW_MANUAL),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS] is not None:
            gear = max(1, min(5, round(int(kwargs[ATTR_BRIGHTNESS]) * 5 / 255)))
            await self.coordinator.async_set_atmosphere(True, gear=gear)
        else:
            await self.coordinator.async_set_atmosphere(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_atmosphere(False)
