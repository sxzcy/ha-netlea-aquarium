"""Sensor platform for Netlea Aquarium."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import current_trip_name, mode_summary, split_addresses
from .const import DOMAIN
from .coordinator import NetleaDataUpdateCoordinator
from .entity import NetleaBaseEntity


def _last_control(device: dict[str, Any]) -> dict[str, Any]:
    value = device.get("_last_control")
    return value if isinstance(value, dict) else {}


def _online_count(device: dict[str, Any]) -> int:
    status = device.get("statusLog") if isinstance(device.get("statusLog"), dict) else {}
    address = status.get("onLineRealAddress") or device.get("controlRealAddress") or device.get("realAddress")
    return len(split_addresses(address))


@dataclass(frozen=True, kw_only=True)
class NetleaSensorEntityDescription(SensorEntityDescription):
    """Netlea sensor description."""

    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[NetleaSensorEntityDescription, ...] = (
    NetleaSensorEntityDescription(
        key="run_mode",
        translation_key="run_mode",
        icon="mdi:state-machine",
        value_fn=mode_summary,
    ),
    NetleaSensorEntityDescription(
        key="current_trip",
        translation_key="current_trip",
        icon="mdi:calendar-clock",
        value_fn=lambda device: current_trip_name(device) or "无",
    ),
    NetleaSensorEntityDescription(
        key="reply_count",
        translation_key="reply_count",
        icon="mdi:counter",
        value_fn=lambda device: _last_control(device).get("reply_count"),
    ),
    NetleaSensorEntityDescription(
        key="online_count",
        translation_key="online_count",
        icon="mdi:access-point-network",
        value_fn=_online_count,
    ),
    NetleaSensorEntityDescription(
        key="last_command",
        translation_key="last_command",
        icon="mdi:gesture-tap-button",
        value_fn=lambda device: _last_control(device).get("action"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Netlea sensors."""
    coordinator: NetleaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([NetleaSensor(coordinator, description) for description in SENSORS])


class NetleaSensor(NetleaBaseEntity, SensorEntity):
    """Netlea sensor."""

    entity_description: NetleaSensorEntityDescription

    def __init__(
        self,
        coordinator: NetleaDataUpdateCoordinator,
        description: NetleaSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.device)
