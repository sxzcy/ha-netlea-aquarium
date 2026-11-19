"""DataUpdateCoordinator for Netlea Aquarium."""

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

from . import const
from .client import NetleaAuthError, NetleaClient, NetleaConnectionError, NetleaError
from .protocol import ControlResult, clamp_int, parse_status_log

_LOGGER = logging.getLogger(__name__)

_TEMP_CHANNEL_SPECS: tuple[tuple[str, str, int, int, int], ...] = (
    ("red", const.CONF_TEMP_RED, const.DEFAULT_TEMP_RED, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("white", const.CONF_TEMP_WHITE, const.DEFAULT_TEMP_WHITE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("green", const.CONF_TEMP_GREEN, const.DEFAULT_TEMP_GREEN, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("blue", const.CONF_TEMP_BLUE, const.DEFAULT_TEMP_BLUE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("fan", const.CONF_TEMP_FAN, const.DEFAULT_TEMP_FAN, const.MIN_TEMP_FAN, const.MAX_TEMP_FAN),
)


class NetleaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator managing polling and control for one Netlea device group."""

    def __init__(self, hass: HomeAssistant, client: NetleaClient, entry: ConfigEntry) -> None:
        options = dict(getattr(entry, "options", None) or {})
        scan_interval = clamp_int(
            options.get(const.CONF_SCAN_INTERVAL),
            const.DEFAULT_SCAN_INTERVAL,
            const.MIN_SCAN_INTERVAL,
            const.MAX_SCAN_INTERVAL,
        )
        super().__init__(
            hass,
            _LOGGER,
            name=const.DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry = entry
        self.stale_failures = clamp_int(
            options.get(const.CONF_STALE_FAILURES),
            const.DEFAULT_STALE_FAILURES,
            const.MIN_STALE_FAILURES,
            const.MAX_STALE_FAILURES,
        )
        self.temp_channels = {
            param: clamp_int(options.get(key), default, low, high)
            for param, key, default, low, high in _TEMP_CHANNEL_SPECS
        }
        self.equipment_user_id = str(entry.data.get(const.CONF_EQUIPMENT_USER_ID) or "")
        self.control_real_address = str(entry.data.get(const.CONF_CONTROL_REAL_ADDRESS) or "").upper()
        self.last_control: dict[str, Any] | None = None
        self.connection_failures = 0

    @property
    def device_key(self) -> str:
        return self.equipment_user_id or self.control_real_address

    @property
    def target_address(self) -> str:
        return str((self.data or {}).get("controlRealAddress") or self.control_real_address).upper()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest device group data from Netlea cloud."""
        try:
            devices = await self.client.async_fetch_devices()
        except NetleaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except NetleaConnectionError as err:
            self.connection_failures += 1
            if self.data and self.connection_failures <= self.stale_failures:
                stale_data = deepcopy(self.data)
                stale_data["_stale"] = True
                stale_data["_connection_failures"] = self.connection_failures
                stale_data["_last_update_error"] = str(err)
                stale_data["_last_update_error_time"] = dt_util.now().isoformat()
                _LOGGER.debug(
                    "Keeping stale Netlea data after failure %s/%s: %s",
                    self.connection_failures, self.stale_failures, err,
                )
                return stale_data
            raise UpdateFailed(str(err)) from err
        except NetleaError as err:
            raise UpdateFailed(str(err)) from err

        self.connection_failures = 0
        for device in devices:
            eq_id = str(device.get("equipmentUserId") or "")
            addr = str(device.get("controlRealAddress") or "").upper()
            if (self.equipment_user_id and eq_id == self.equipment_user_id) or (
                self.control_real_address and addr == self.control_real_address
            ):
                device["_stale"] = False
                device["_connection_failures"] = 0
                if self.last_control:
                    device["_last_control"] = self.last_control
                return device

        raise UpdateFailed("Netlea device group not found")

    async def async_temp_on(self) -> ControlResult:
        return await self._async_control(
            const.ACTION_TEMP_ON, lambda target: self.client.async_temp_on(target, **self.temp_channels)
        )

    async def async_temp_off(self) -> ControlResult:
        return await self._async_control(const.ACTION_TEMP_OFF, self.client.async_temp_off)

    async def async_resume_schedule(self) -> ControlResult:
        return await self._async_control(
            const.ACTION_RESUME_SCHEDULE,
            lambda target: self.client.async_resume_schedule(target, parse_status_log(self.data), now=dt_util.now()),
        )

    async def _async_control(self, action: str, call: Callable[[str], Awaitable[ControlResult]]) -> ControlResult:
        target_address = self.target_address
        if not target_address:
            raise HomeAssistantError("缺少尼特利控制地址，无法发送控制命令")
        try:
            result = await call(target_address)
        except NetleaAuthError as err:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError("尼特利认证失败，请重新登录") from err
        except NetleaError as err:
            raise HomeAssistantError(f"尼特利控制失败：{err}") from err
        self._apply_control_result(action, result)
        return result

    def _apply_control_result(self, action: str, result: ControlResult) -> None:
        data = deepcopy(self.data or {})
        status = parse_status_log(data)
        master = (result.legacy_state or {}).get("master") if isinstance(result.legacy_state, dict) else None
        if isinstance(master, dict):
            pattern = int(master.get("pattern") or 0)
            status["onoff"] = int(master.get("lampState") or 0)
            status["travelRunning"] = {**(status.get("travelRunning") if isinstance(status.get("travelRunning"), dict) else {}), "main": pattern == 2}
            status["manualStatus"] = {**(status.get("manualStatus") if isinstance(status.get("manualStatus"), dict) else {}), "main": pattern == 3}
        self.last_control = {
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
        data["_last_control"] = self.last_control
        self.async_set_updated_data(data)
