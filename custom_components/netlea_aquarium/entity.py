"""Base entities for Netlea Aquarium."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import parse_status_log
from .const import DOMAIN
from .coordinator import NetleaDataUpdateCoordinator


class NetleaBaseEntity(CoordinatorEntity[NetleaDataUpdateCoordinator]):
    """Base class for Netlea entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NetleaDataUpdateCoordinator, suffix: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_key}_{suffix}"

    @property
    def device(self) -> dict[str, Any]:
        """Return current device data."""
        return self.coordinator.data or {}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        status = parse_status_log(self.device)
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_key)},
            manufacturer="Netlea",
            name=str(self.device.get("name") or "Netlea Aquarium"),
            model=str(status.get("showName") or self.device.get("name") or "Aquarium Light"),
        )
