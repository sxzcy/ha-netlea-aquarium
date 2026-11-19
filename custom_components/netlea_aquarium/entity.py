"""Base entity for Netlea Aquarium."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NetleaCoordinator
from .protocol import parse_status_log


class NetleaEntity(CoordinatorEntity[NetleaCoordinator]):
    """Base entity bound to a NetleaCoordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NetleaCoordinator, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_key}_{suffix}"

    @property
    def device(self) -> dict[str, Any]:
        return self.coordinator.data or {}

    @property
    def available(self) -> bool:
        return super().available

    @property
    def device_info(self) -> DeviceInfo:
        status = parse_status_log(self.device)
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_key)},
            manufacturer="Netlea",
            name=str(self.device.get("name") or "尼特利鱼缸"),
            model=str(status.get("showName") or self.device.get("name") or "Aquarium Light"),
        )
