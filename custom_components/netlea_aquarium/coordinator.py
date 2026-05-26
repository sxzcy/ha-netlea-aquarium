"""Coordinator for Netlea Aquarium."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    ControlResult,
    NetleaAuthError,
    NetleaClient,
    NetleaConnectionError,
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
    DEFAULT_STALE_FAILURES,
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
        self._connection_failures = 0

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
        except NetleaConnectionError as err:
            self._connection_failures += 1
            if self.data and self._connection_failures <= DEFAULT_STALE_FAILURES:
                data = deepcopy(self.data)
                data["_stale"] = True
                data["_connection_failures"] = self._connection_failures
                data["_last_update_error"] = str(err)
                data["_last_update_error_time"] = dt_util.now().isoformat()
                _LOGGER.debug(
                    "Keeping stale Netlea data after temporary connection failure %s/%s: %s",
                    self._connection_failures,
                    DEFAULT_STALE_FAILURES,
                    err,
                )
                return data
            raise UpdateFailed(str(err)) from err
        except NetleaError as err:
            raise UpdateFailed(str(err)) from err

        self._connection_failures = 0
        for device in devices:
            equipment_user_id = str(device.get("equipmentUserId") or "")
            real_address = str(device.get("controlRealAddress") or "").upper()
            if equipment_user_id == self.equipment_user_id or real_address == self.control_real_address:
                device["_stale"] = False
                device["_connection_failures"] = 0
                if self._last_control:
                    device["_last_control"] = self._last_control
                return device

        raise UpdateFailed("Netlea device group not found")

    async def async_temp_on(self) -> ControlResult:
        """Temporarily turn on the main light."""
        return await self._async_control(ACTION_TEMP_ON, self.client.async_temp_on)

    async def async_temp_off(self) -> ControlResult:
        """Temporarily turn off the main light."""
        return await self._async_control(ACTION_TEMP_OFF, self.client.async_temp_off)

    async def async_resume_schedule(self) -> ControlResult:
        """Resume schedule."""
        async def _resume(target_address: str) -> ControlResult:
            return await self.client.async_resume_schedule(target_address, self.data)

        return await self._async_control(ACTION_RESUME_SCHEDULE, _resume)

    async def _async_control(
        self,
        action: str,
        call: Callable[[str], Awaitable[ControlResult]],
    ) -> ControlResult:
        """Run one user-triggered control command."""
        target_address = self.target_address
        if not target_address:
            raise HomeAssistantError("缺少尼特利控制地址，无法发送控制命令")
        try:
            result = await call(target_address)
        except NetleaAuthError as err:
            raise HomeAssistantError("尼特利认证失败，请重新登录") from err
        except (NetleaConnectionError, NetleaError) as err:
            raise HomeAssistantError(f"尼特利控制失败：{err}") from err
        self._apply_control_result(action, result)
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
            "expected_addresses": result.expected_addresses,
            "replied_addresses": result.replied_addresses,
            "missing_addresses": result.missing_addresses,
            "legacy_state": result.legacy_state,
            "time": dt_util.now().isoformat(),
        }
        data["statusLog"] = status
        data["_last_control"] = self._last_control
        self.async_set_updated_data(data)
