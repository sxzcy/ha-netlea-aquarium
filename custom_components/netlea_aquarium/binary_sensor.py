"""Binary sensor platform for Netlea Aquarium."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NetleaConfigEntry
from .coordinator import NetleaCoordinator
from .entity import NetleaEntity
from .protocol import has_main_travel, is_main_travel_running


@dataclass(frozen=True, kw_only=True)
class NetleaBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Declarative Netlea binary sensor description."""

    value_fn: Callable[[dict[str, Any]], bool]


BINARY_SENSORS: tuple[NetleaBinarySensorEntityDescription, ...] = (
    NetleaBinarySensorEntityDescription(
        key="has_travel", translation_key="has_travel", icon="mdi:calendar-check-outline", value_fn=has_main_travel
    ),
    NetleaBinarySensorEntityDescription(
        key="travel_running", translation_key="travel_running", icon="mdi:calendar-play-outline", value_fn=is_main_travel_running
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: NetleaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Netlea binary sensors."""
    async_add_entities(NetleaBinarySensor(entry.runtime_data, desc) for desc in BINARY_SENSORS)


class NetleaBinarySensor(NetleaEntity, BinarySensorEntity):
    """Netlea schedule binary sensor."""

    entity_description: NetleaBinarySensorEntityDescription

    def __init__(self, coordinator: NetleaCoordinator, description: NetleaBinarySensorEntityDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self.device)
