"""Binary sensor platform for Netlea Aquarium."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import has_main_travel, is_main_travel_running
from .const import DOMAIN
from .coordinator import NetleaDataUpdateCoordinator
from .entity import NetleaBaseEntity


@dataclass(frozen=True, kw_only=True)
class NetleaBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Netlea binary sensor description."""

    value_fn: Callable[[dict], bool]


BINARY_SENSORS: tuple[NetleaBinarySensorEntityDescription, ...] = (
    NetleaBinarySensorEntityDescription(
        key="has_travel",
        translation_key="has_travel",
        icon="mdi:calendar-check",
        value_fn=has_main_travel,
    ),
    NetleaBinarySensorEntityDescription(
        key="travel_running",
        translation_key="travel_running",
        icon="mdi:calendar-play",
        value_fn=is_main_travel_running,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Netlea binary sensors."""
    coordinator: NetleaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([NetleaBinarySensor(coordinator, description) for description in BINARY_SENSORS])


class NetleaBinarySensor(NetleaBaseEntity, BinarySensorEntity):
    """Netlea binary sensor."""

    entity_description: NetleaBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: NetleaDataUpdateCoordinator,
        description: NetleaBinarySensorEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return binary sensor state."""
        return self.entity_description.value_fn(self.device)
