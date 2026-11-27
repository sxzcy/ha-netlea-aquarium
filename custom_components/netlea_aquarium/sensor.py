"""Sensor platform for Netlea Aquarium."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import NetleaConfigEntry, presets, protocol as proto
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity


def _last_control(device: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance((value := device.get("_last_control")), dict) else {}


def _power_w(device: dict[str, Any]) -> int:
    r, w, g, b, f_pwm = proto.live_master_channels(device)
    if proto.is_main_light_on(device) is False:
        return 0
    return presets.estimate_power_w(r, w, g, b, f_pwm)


def _wifi_rssi(device: dict[str, Any]) -> int | None:
    val = proto.parse_status_log(device).get("_wifi_rssi")
    return int(val) if val is not None else None


def _firmware_version(device: dict[str, Any]) -> str | None:
    status = proto.parse_status_log(device)
    val = status.get("_firmware_version") or device.get("firmwareVersion")
    return str(val) if val else None


@dataclass(frozen=True, kw_only=True)
class NetleaSensorEntityDescription(SensorEntityDescription):
    """Declarative Netlea sensor description."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[NetleaSensorEntityDescription, ...] = (
    NetleaSensorEntityDescription(
        key="run_mode",
        translation_key="run_mode",
        icon="mdi:state-machine",
        value_fn=lambda d: proto.mode_summary(d, dt_util.now()),
    ),
    NetleaSensorEntityDescription(
        key="current_trip",
        translation_key="current_trip",
        icon="mdi:calendar-clock-outline",
        value_fn=lambda d: proto.current_trip_name(d, dt_util.now()) or "无",
    ),
    NetleaSensorEntityDescription(
        key="power_w",
        translation_key="power_w",
        icon="mdi:flash-outline",
        native_unit_of_measurement="W",
        value_fn=_power_w,
    ),
    NetleaSensorEntityDescription(
        key="online_count",
        translation_key="online_count",
        icon="mdi:access-point-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=proto.online_lamp_count,
    ),
    NetleaSensorEntityDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        icon="mdi:wifi",
        native_unit_of_measurement="dBm",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_wifi_rssi,
    ),
    NetleaSensorEntityDescription(
        key="firmware_version",
        translation_key="firmware_version",
        icon="mdi:chip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_firmware_version,
    ),
    NetleaSensorEntityDescription(
        key="transport_mode",
        translation_key="transport_mode",
        icon="mdi:lan-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: str(d.get("_transport") or "cloud_ws"),
    ),
    NetleaSensorEntityDescription(
        key="reply_count",
        translation_key="reply_count",
        icon="mdi:check-network-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _last_control(d).get("reply_count"),
    ),
    NetleaSensorEntityDescription(
        key="last_command",
        translation_key="last_command",
        icon="mdi:gesture-tap-button",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _last_control(d).get("action"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Netlea sensors."""
    async_add_entities(NetleaSensor(entry.runtime_data, desc) for desc in SENSORS)


class NetleaSensor(NetleaEntity, SensorEntity):
    """Netlea status or diagnostic sensor."""

    entity_description: NetleaSensorEntityDescription

    def __init__(self, coordinator: NetleaCoordinator, description: NetleaSensorEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.device)
