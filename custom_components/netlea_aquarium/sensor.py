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

from . import NetleaConfigEntry
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity
from .protocol import current_trip_name, mode_summary, online_lamp_count


def _last_control(device: dict[str, Any]) -> dict[str, Any]:
    return value if isinstance((value := device.get("_last_control")), dict) else {}


@dataclass(frozen=True, kw_only=True)
class NetleaSensorEntityDescription(SensorEntityDescription):
    """Declarative Netlea sensor description."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[NetleaSensorEntityDescription, ...] = (
    NetleaSensorEntityDescription(
        key="run_mode", translation_key="run_mode", icon="mdi:state-machine", value_fn=lambda d: mode_summary(d, dt_util.now())
    ),
    NetleaSensorEntityDescription(
        key="current_trip", translation_key="current_trip", icon="mdi:calendar-clock-outline", value_fn=lambda d: current_trip_name(d, dt_util.now()) or "无"
    ),
    NetleaSensorEntityDescription(
        key="online_count", translation_key="online_count", icon="mdi:access-point-network", entity_category=EntityCategory.DIAGNOSTIC, value_fn=online_lamp_count
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


async def async_setup_entry(hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
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
