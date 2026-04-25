"""Coordinator for Netlea Aquarium."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ControlResult,
    NetleaAuthError,
    NetleaClient,
    NetleaError,
    parse_status_log,
)
from .const import (
    ACTION_RESUME_SCHEDULE,
    ACTION_TEMP_OFF,
    ACTION_TEMP_ON,
    CONF_CONTROL_REAL_ADDRESS,
    CONF_EQUIPMENT_USER_ID,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class NetleaDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Data update coordinator for one Netlea device group."""

    def __init__(self, hass: HomeAssistant, client: NetleaClient, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.entry = entry
        self.equipment_user_id = str(entry.data.get(CONF_EQUIPMENT_USER_ID) or "")
        self.control_real_address = str(entry.data.get(CONF_CONTROL_REAL_ADDRESS) or "").upper()
        self._last_control: dict[str, Any] | None = None

    @property
    def device_key(self) -> str:
        """Return stable device key."""
        return self.equipment_user_id or self.control_real_address

    @property
    def target_address(self) -> str:
        """Return current websocket target address."""
        data = self.data or {}
        return str(data.get("controlRealAddress") or self.control_real_address).upper()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest data from Netlea."""
        try:
            devices = await self.client.async_fetch_devices()
        except NetleaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NetleaError as err:
            raise UpdateFailed(str(err)) from err

        for device in devices:
            equipment_user_id = str(device.get("equipmentUserId") or "")
            real_address = str(device.get("controlRealAddress") or "").upper()
            if equipment_user_id == self.equipment_user_id or real_address == self.control_real_address:
                if self._last_control:
                    device["_last_control"] = self._last_control
                return device

        raise UpdateFailed("Netlea device group not found")

    async def async_temp_on(self) -> ControlResult:
        """Temporarily turn on the main light."""
        result = await self.client.async_temp_on(self.target_address)
        self._apply_control_result(ACTION_TEMP_ON, result)
        return result

    async def async_temp_off(self) -> ControlResult:
        """Temporarily turn off the main light."""
        result = await self.client.async_temp_off(self.target_address)
        self._apply_control_result(ACTION_TEMP_OFF, result)
        return result

    async def async_resume_schedule(self) -> ControlResult:
        """Resume schedule."""
        result = await self.client.async_resume_schedule(self.target_address, self.data)
        self._apply_control_result(ACTION_RESUME_SCHEDULE, result)
        return result

    def _apply_control_result(self, action: str, result: ControlResult) -> None:
        """Apply a command result optimistically to coordinator data."""
        data = deepcopy(self.data or {})
        status = parse_status_log(data)
        legacy_state = result.legacy_state or {}
        master_state = legacy_state.get("master") if isinstance(legacy_state, dict) else None

        if isinstance(master_state, dict):
            status["onoff"] = int(master_state.get("lampState") or 0)
            travel_running = status.get("travelRunning")
            if not isinstance(travel_running, dict):
                travel_running = {}
            travel_running["main"] = int(master_state.get("pattern") or 0) == 2
            status["travelRunning"] = travel_running

            manual_status = status.get("manualStatus")
            if not isinstance(manual_status, dict):
                manual_status = {}
            manual_status["main"] = int(master_state.get("pattern") or 0) == 3
            status["manualStatus"] = manual_status

        self._last_control = {
            "action": action,
            "frame": result.frame,
            "reply_count": len(result.replies),
            "replied_addresses": result.replied_addresses,
            "legacy_state": result.legacy_state,
            "time": dt_util.now().isoformat(),
        }
        data["statusLog"] = status
        data["_last_control"] = self._last_control
        self.async_set_updated_data(data)
