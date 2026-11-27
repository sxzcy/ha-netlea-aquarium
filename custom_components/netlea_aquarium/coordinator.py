"""DataUpdateCoordinator for Netlea Aquarium (v2.0.0 multi-transport + full control)."""

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

from . import const, presets, protocol as proto
from .client import NetleaAuthError, NetleaClient, NetleaConnectionError, NetleaError
from .transport import CloudWsTransport, NetleaTransport

_LOGGER = logging.getLogger(__name__)

_TEMP_CHANNEL_SPECS: tuple[tuple[str, str, int, int, int], ...] = (
    ("red", const.CONF_TEMP_RED, const.DEFAULT_TEMP_RED, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("white", const.CONF_TEMP_WHITE, const.DEFAULT_TEMP_WHITE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("green", const.CONF_TEMP_GREEN, const.DEFAULT_TEMP_GREEN, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("blue", const.CONF_TEMP_BLUE, const.DEFAULT_TEMP_BLUE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    ("fan", const.CONF_TEMP_FAN, const.DEFAULT_TEMP_FAN, const.MIN_TEMP_FAN, const.MAX_TEMP_FAN),
)


class NetleaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator managing state sync and control for one Netlea device group."""

    def __init__(
        self,
        hass: HomeAssistant,
        client_or_transport: NetleaClient | NetleaTransport,
        entry: ConfigEntry,
        *,
        client: NetleaClient | None = None,
    ) -> None:
        options = dict(getattr(entry, "options", None) or {})
        data = dict(getattr(entry, "data", None) or {})
        scan_interval = proto.clamp_int(
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
        if isinstance(client_or_transport, NetleaTransport):
            self.transport: NetleaTransport = client_or_transport
            self.client: NetleaClient | None = client or (
                client_or_transport.client if isinstance(client_or_transport, CloudWsTransport) else None
            )
        else:
            # Backward-compatible constructor signature NetleaCoordinator(hass, client, entry)
            self.client = client_or_transport
            self.transport = CloudWsTransport(client_or_transport)

        self.entry = entry
        self.transport_mode: str = self.transport.transport_type
        self.model_name: str = str(
            options.get(const.CONF_MODEL) or data.get(const.CONF_MODEL) or const.DEFAULT_MODEL
        )
        self.stale_failures = proto.clamp_int(
            options.get(const.CONF_STALE_FAILURES),
            const.DEFAULT_STALE_FAILURES,
            const.MIN_STALE_FAILURES,
            const.MAX_STALE_FAILURES,
        )
        self.temp_channels = {
            param: proto.clamp_int(options.get(key), default, low, high)
            for param, key, default, low, high in _TEMP_CHANNEL_SPECS
        }
        self.temp_on_minutes: int = proto.clamp_int(
            options.get(const.CONF_TEMP_ON_MINUTES),
            const.DEFAULT_TEMP_ON_MINUTES,
            const.MIN_TEMP_MINUTES,
            const.MAX_TEMP_MINUTES,
        )
        raw_mode = str(options.get(const.CONF_CONTROL_MODE) or const.CONTROL_MODE_SCHEDULE_AUTO)
        self.control_mode: str = (
            raw_mode if raw_mode in const.VALID_CONTROL_MODES else const.CONTROL_MODE_SCHEDULE_AUTO
        )
        self.equipment_user_id = str(data.get(const.CONF_EQUIPMENT_USER_ID) or "")
        self.control_real_address = ",".join(
            proto.split_addresses(
                options.get(const.CONF_CONTROL_REAL_ADDRESS) or data.get(const.CONF_CONTROL_REAL_ADDRESS) or ""
            )
        )
        self.device_name = str(data.get(const.CONF_DEVICE_NAME) or entry.title or "尼特利鱼缸")
        self.last_control: dict[str, Any] | None = None
        self.connection_failures = 0
        self.active_preset_name: str = presets.preset_names_for_model(self.model_name)[0]
        self.active_preset_grade: int = 10
        self.offline_voice_enabled: bool = True
        self._schedule_loaded_local: bool = False
        self.transport.set_push_callback(self._on_transport_push)

    @property
    def device_key(self) -> str:
        return self.equipment_user_id or self.control_real_address

    @property
    def target_address(self) -> str:
        return str((self.data or {}).get("controlRealAddress") or self.control_real_address).upper()

    def _base_local_device(self) -> dict[str, Any]:
        existing = deepcopy(self.data) if isinstance(self.data, dict) else {}
        status = proto.parse_status_log(existing)
        status.setdefault("showName", self.model_name)
        return {
            **existing,
            "equipmentUserId": self.equipment_user_id or self.device_key,
            "name": existing.get("name") or self.device_name,
            "realAddress": self.control_real_address,
            "controlRealAddress": self.control_real_address,
            "statusLog": status,
            "_stale": False,
            "_connection_failures": 0,
            "_transport": self.transport_mode,
        }

    def _on_transport_push(self, mac: str, kind: str, frame_hex: str) -> None:
        """Handle real-time local_push frames from Local MQTT or Local BLE."""
        device = self._base_local_device()
        status = proto.parse_status_log(device)
        online = self.transport.connected_addresses()
        if kind == "disconnect":
            proto.apply_decoded_state_to_status(status, online_addresses=online)
        elif kind == "heartbeat":
            hb = proto.decode_heartbeat_0x0a(frame_hex)
            proto.apply_decoded_state_to_status(status, heartbeat_0x0a=hb, online_addresses=online)
        elif frame_hex:
            cmd = int(frame_hex[6:8], 16) if len(frame_hex) >= 8 else 0
            if cmd == 0x14:
                state = proto.decode_lamps_state_reply(frame_hex)
                proto.apply_decoded_state_to_status(status, state, online_addresses=online)
                self._sync_preset_tracker(state)
            elif cmd == 0x12:
                sched = proto.decode_schedule_0x12(frame_hex, self.model_name)
                if sched:
                    self._schedule_loaded_local = True
                proto.apply_decoded_state_to_status(status, schedule_0x12=sched, online_addresses=online)
            elif cmd == 0x1A:
                tmp = proto.decode_temp_0x1a(frame_hex)
                if tmp and tmp.get("on_minutes"):
                    self.temp_on_minutes = proto.clamp_int(
                        tmp["on_minutes"], self.temp_on_minutes, const.MIN_TEMP_MINUTES, const.MAX_TEMP_MINUTES
                    )
        device["statusLog"] = status
        if self.last_control:
            device["_last_control"] = self.last_control
        self.async_set_updated_data(device)

    def _sync_preset_tracker(self, state_0x14: dict[str, Any] | None) -> None:
        if not isinstance(state_0x14, dict):
            return
        master = state_0x14.get("master")
        if not isinstance(master, dict):
            return
        matched = presets.match_preset_from_channels(
            self.model_name,
            int(master.get("R", 0)),
            int(master.get("W", 0)),
            int(master.get("G", 0)),
            int(master.get("B", 0)),
        )
        if matched:
            self.active_preset_name, self.active_preset_grade = matched

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh state from active transport (Local MQTT / Local BLE / Cloud WS)."""
        if self.transport_mode == const.TRANSPORT_LOCAL_MQTT:
            device = self._base_local_device()
            status = proto.parse_status_log(device)
            online = self.transport.connected_addresses()
            proto.apply_decoded_state_to_status(status, online_addresses=online)
            if online:
                try:
                    res = await self.transport.async_send_frame(
                        ",".join(online), proto.build_query_state_frame(), timeout=3.0, expect_cmd=0x14
                    )
                    proto.apply_decoded_state_to_status(status, res.legacy_state, online_addresses=online)
                    self._sync_preset_tracker(res.legacy_state)
                    if not self._schedule_loaded_local:
                        sched_res = await self.transport.async_send_frame(
                            online[0], proto.build_query_schedule_0x25(1), timeout=3.0, expect_cmd=0x12
                        )
                        for rep in sched_res.replies:
                            msg = (rep.get("msgBody") or {}).get("message")
                            if sched := proto.decode_schedule_0x12(msg, self.model_name):
                                proto.apply_decoded_state_to_status(status, schedule_0x12=sched, online_addresses=online)
                                self._schedule_loaded_local = True
                                break
                except NetleaError as err:
                    _LOGGER.debug("Periodic Local MQTT poll skipped: %s", err)
            device["statusLog"] = status
            if self.last_control:
                device["_last_control"] = self.last_control
            return device

        if self.transport_mode == const.TRANSPORT_LOCAL_BLE:
            device = self._base_local_device()
            status = proto.parse_status_log(device)
            try:
                res = await self.transport.async_send_frame(
                    self.target_address, proto.build_query_state_frame(), timeout=8.0, expect_cmd=0x14
                )
                proto.apply_decoded_state_to_status(
                    status, res.legacy_state, online_addresses=res.replied_addresses
                )
                self._sync_preset_tracker(res.legacy_state)
                if not self._schedule_loaded_local and res.replied_addresses:
                    try:
                        sres = await self.transport.async_send_frame(
                            res.replied_addresses[0],
                            proto.build_query_schedule_0x25(1),
                            timeout=8.0,
                            expect_cmd=0x12,
                        )
                        for rep in sres.replies:
                            msg = (rep.get("msgBody") or {}).get("message")
                            if sched := proto.decode_schedule_0x12(msg, self.model_name):
                                proto.apply_decoded_state_to_status(status, schedule_0x12=sched)
                                self._schedule_loaded_local = True
                                break
                    except NetleaError:
                        pass
                self.connection_failures = 0
                device["statusLog"] = status
                if self.last_control:
                    device["_last_control"] = self.last_control
                return device
            except NetleaError as err:
                self.connection_failures += 1
                if self.data and self.connection_failures <= self.stale_failures:
                    stale_data = deepcopy(self.data)
                    stale_data["_stale"] = True
                    stale_data["_connection_failures"] = self.connection_failures
                    stale_data["_last_update_error"] = str(err)
                    stale_data["_last_update_error_time"] = dt_util.now().isoformat()
                    return stale_data
                raise UpdateFailed(str(err)) from err

        # Cloud WS/REST mode (v1.0.0 behavior strictly preserved)
        if self.client is None:
            raise UpdateFailed("Missing Netlea cloud client")
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
                    self.connection_failures,
                    self.stale_failures,
                    err,
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
                device["_transport"] = self.transport_mode
                # Preserve live master/atmosphere decoded state if already captured
                old_status = proto.parse_status_log(self.data)
                new_status = proto.parse_status_log(device)
                for keep_key in ("_live_master", "_live_atmosphere", "_wifi_rssi", "_firmware_version"):
                    if keep_key in old_status and keep_key not in new_status:
                        new_status[keep_key] = old_status[keep_key]
                device["statusLog"] = new_status
                if self.last_control:
                    device["_last_control"] = self.last_control
                return device

        raise UpdateFailed("Netlea device group not found")

    async def async_temp_on(self) -> proto.ControlResult:
        """Temporarily turn on the main viewing light with configured defaults."""
        if self.transport_mode == const.TRANSPORT_CLOUD_WS and self.client is not None:
            return await self._async_control(
                const.ACTION_TEMP_ON,
                lambda target: self.client.async_temp_on(target, **self.temp_channels),  # type: ignore[union-attr]
            )
        frame = proto.build_temp_light_frame(
            True,
            red=self.temp_channels["red"],
            white=self.temp_channels["white"],
            green=self.temp_channels["green"],
            blue=self.temp_channels["blue"],
            fan=self.temp_channels["fan"],
            on_minutes=self.temp_on_minutes,
            off_minutes=self.temp_on_minutes,
        )
        return await self._async_control(
            const.ACTION_TEMP_ON,
            lambda target: self.transport.async_send_frame(target, frame, expect_cmd=0x14),
        )

    async def async_temp_off(self) -> proto.ControlResult:
        """Temporarily turn off the main light."""
        if self.transport_mode == const.TRANSPORT_CLOUD_WS and self.client is not None:
            return await self._async_control(
                const.ACTION_TEMP_OFF,
                self.client.async_temp_off,
            )
        frame = proto.build_temp_light_frame(
            False, on_minutes=self.temp_on_minutes, off_minutes=self.temp_on_minutes
        )
        return await self._async_control(
            const.ACTION_TEMP_OFF,
            lambda target: self.transport.async_send_frame(target, frame, expect_cmd=0x14),
        )

    async def async_resume_schedule(self) -> proto.ControlResult:
        """Resume the lamp's configured 24-hour schedule."""
        frame = proto.build_resume_schedule_frame(proto.parse_status_log(self.data), now=dt_util.now())
        if self.transport_mode == const.TRANSPORT_CLOUD_WS and self.client is not None:
            return await self._async_control(
                const.ACTION_RESUME_SCHEDULE,
                lambda target: self.client.async_resume_schedule(  # type: ignore[union-attr]
                    target, proto.parse_status_log(self.data), now=dt_util.now()
                ),
            )
        return await self._async_control(
            const.ACTION_RESUME_SCHEDULE,
            lambda target: self.transport.async_send_frame(target, frame, expect_cmd=0x14),
        )

    async def async_set_main_channels(
        self,
        red: int,
        white: int,
        green: int,
        blue: int,
        fan: int | None = None,
        *,
        mode_override: str | None = None,
        duration_minutes: int | None = None,
        mode_id: int = 0,
        grade: int = 0,
    ) -> proto.ControlResult:
        """Set main RGBW + fan channels respecting Option C (schedule_auto -> 0x1A, manual_hold -> 0x10)."""
        eff_mode = mode_override or self.control_mode
        if eff_mode == const.CONTROL_MODE_MANUAL_HOLD:
            frame = proto.build_manual_light_frame(
                True, red, white, green, blue, fan, mode_id=mode_id, grade=grade
            )
            action = "manual_channels"
        else:
            dur = duration_minutes if duration_minutes is not None else self.temp_on_minutes
            frame = proto.build_temp_light_frame(
                True,
                red=red,
                white=white,
                green=green,
                blue=blue,
                fan=fan,
                on_minutes=dur,
                off_minutes=dur,
                mode_id=mode_id or const.DEFAULT_TEMP_MODE_ID,
                grade=grade or const.DEFAULT_TEMP_GRADE,
            )
            action = "temp_channels"
        return await self._async_control(
            action,
            lambda target: self.transport.async_send_frame(target, frame, expect_cmd=0x14),
        )

    async def async_apply_preset(
        self,
        preset_name: str,
        grade: int | None = None,
        *,
        mode_override: str | None = None,
        duration_minutes: int | None = None,
    ) -> proto.ControlResult:
        """Apply a named official spectrum preset at the given grade."""
        target_grade = self.active_preset_grade if grade is None else int(grade)
        pg = presets.get_preset_grade(self.model_name, preset_name, target_grade)
        if pg is None:
            raise HomeAssistantError(f"未知光谱预设：{preset_name}")
        self.active_preset_name = preset_name
        self.active_preset_grade = target_grade
        return await self.async_set_main_channels(
            pg.red,
            pg.white,
            pg.green,
            pg.blue,
            pg.fan_pwm,
            mode_override=mode_override,
            duration_minutes=duration_minutes,
            mode_id=1,
            grade=target_grade,
        )

    async def async_set_fan_gear(self, gear: int) -> proto.ControlResult:
        """Adjust cooling fan gear (0..10) while keeping current RGBW channels and enforcing thermal safety."""
        r, w, g, b, _ = proto.live_master_channels(self.data or {})
        if r == w == g == b == 0:
            r = self.temp_channels["red"]
            w = self.temp_channels["white"]
            g = self.temp_channels["green"]
            b = self.temp_channels["blue"]
        f_pwm = presets.fan_gear_to_pwm(gear)
        return await self.async_set_main_channels(r, w, g, b, f_pwm)

    async def async_set_atmosphere(
        self, on: bool, *, gear: int | None = None, pwm: int | None = None
    ) -> proto.ControlResult:
        """Control atmosphere light; automatically exits follow mode (0x22(0)) before manual 0x1E if needed."""
        atmos = proto.live_atmosphere_state(self.data or {})
        cur_pwm = atmos.get("moodLamp") or presets.atmos_gear_to_pwm(3)
        if pwm is not None:
            target_pwm = max(0, min(255, int(pwm)))
            target_gear = presets.atmos_pwm_to_gear(target_pwm)
        elif gear is not None:
            target_gear = max(0, min(5, int(gear)))
            target_pwm = presets.atmos_gear_to_pwm(target_gear)
        else:
            target_pwm = cur_pwm if on else 0
            target_gear = presets.atmos_pwm_to_gear(target_pwm)

        if atmos.get("followType", 0) != 0:
            exit_frame = proto.build_atmosphere_follow_0x22(0, target_pwm if on else cur_pwm, target_gear)
            await self._async_control(
                "atmosphere_exit_follow",
                lambda target: self.transport.async_send_frame(target, exit_frame, expect_cmd=0x14),
            )
        frame = proto.build_atmosphere_manual_0x1e(on, target_pwm, target_gear)
        return await self._async_control(
            "atmosphere_on" if on else "atmosphere_off",
            lambda target: self.transport.async_send_frame(target, frame, expect_cmd=0x14),
        )

    async def async_set_atmosphere_follow(self, follow_mode: str) -> proto.ControlResult:
        """Set atmosphere follow mode (manual / follow_main_on / follow_main_off) via 0x22."""
        code = const.ATMOS_FOLLOW_TO_CODE.get(follow_mode)
        if code is None:
            raise HomeAssistantError(f"无效的氛围灯跟随模式：{follow_mode}")
        atmos = proto.live_atmosphere_state(self.data or {})
        cur_pwm = atmos.get("moodLamp") or presets.atmos_gear_to_pwm(3)
        cur_gear = presets.atmos_pwm_to_gear(cur_pwm)
        frame = proto.build_atmosphere_follow_0x22(code, cur_pwm, cur_gear)
        return await self._async_control(
            f"atmosphere_follow_{follow_mode}",
            lambda target: self.transport.async_send_frame(target, frame, expect_cmd=0x14),
        )

    async def async_set_offline_voice(self, enabled: bool) -> proto.ControlResult:
        """Enable or disable offline voice prompts via 0x17."""
        frame = proto.build_voice_config_0x17(enabled)
        res = await self._async_control(
            "voice_on" if enabled else "voice_off",
            lambda target: self.transport.async_send_frame(target, frame),
        )
        self.offline_voice_enabled = bool(enabled)
        return res

    async def async_refresh_state(self, *, include_schedule: bool = False) -> proto.ControlResult:
        """Actively poll 0x15 live state (and optionally 0x25 stored schedule)."""
        res = await self._async_control(
            const.ACTION_REFRESH_STATE,
            lambda target: self.transport.async_send_frame(target, proto.build_query_state_frame(), expect_cmd=0x14),
        )
        if include_schedule:
            try:
                sres = await self.transport.async_send_frame(
                    self.target_address, proto.build_query_schedule_0x25(1), expect_cmd=0x12
                )
                for rep in sres.replies:
                    msg = (rep.get("msgBody") or {}).get("message")
                    if sched := proto.decode_schedule_0x12(msg, self.model_name):
                        data = deepcopy(self.data or {})
                        status = proto.parse_status_log(data)
                        proto.apply_decoded_state_to_status(status, schedule_0x12=sched)
                        data["statusLog"] = status
                        self._schedule_loaded_local = True
                        self.async_set_updated_data(data)
                        break
            except NetleaError as err:
                _LOGGER.debug("Optional 0x25 schedule refresh skipped: %s", err)
        return res

    async def async_sync_time(self) -> proto.ControlResult:
        """Synchronize lamp internal RTC clock via 0x04."""
        frame = proto.build_sync_time_0x04(dt_util.now())
        return await self._async_control(
            const.ACTION_SYNC_TIME,
            lambda target: self.transport.async_send_frame(target, frame),
        )

    async def async_set_schedule(
        self,
        trips: list[dict[str, Any]],
        *,
        sunrise_minutes: int = 0,
        sunset_minutes: int = 0,
        resume: bool = True,
    ) -> proto.ControlResult:
        """Write full 1..10 slot 24-hour schedule via 0x12 (with power/fan validation)."""
        frame = proto.build_schedule_frame_0x12(
            trips, sunrise_minutes=sunrise_minutes, sunset_minutes=sunset_minutes
        )
        res = await self._async_control(
            "set_schedule",
            lambda target: self.transport.async_send_frame(target, frame, expect_cmd=0x12),
        )
        sched = proto.decode_schedule_0x12(frame, self.model_name)
        if sched:
            data = deepcopy(self.data or {})
            status = proto.parse_status_log(data)
            proto.apply_decoded_state_to_status(status, schedule_0x12=sched)
            data["statusLog"] = status
            self._schedule_loaded_local = True
            self.async_set_updated_data(data)
        if resume:
            await self.async_resume_schedule()
        return res

    async def _async_control(
        self, action: str, call: Callable[[str], Awaitable[proto.ControlResult]]
    ) -> proto.ControlResult:
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

    def _apply_control_result(self, action: str, result: proto.ControlResult) -> None:
        data = deepcopy(self.data or self._base_local_device())
        status = proto.parse_status_log(data)
        online = (
            self.transport.connected_addresses()
            if self.transport_mode != const.TRANSPORT_CLOUD_WS
            else None
        )
        proto.apply_decoded_state_to_status(status, result.legacy_state, online_addresses=online)
        self._sync_preset_tracker(result.legacy_state)
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
