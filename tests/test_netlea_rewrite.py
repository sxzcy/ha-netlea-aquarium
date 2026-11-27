"""Comprehensive tests for the rewritten Netlea Aquarium integration."""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import types
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LEGACY_API_PATH = (
    REPO_ROOT.parents[1]
    / "ha-plugins-audit"
    / "github"
    / "ha-netlea-aquarium"
    / "custom_components"
    / "netlea_aquarium"
    / "api.py"
)


def _install_stubs() -> None:
    """Install lightweight aiohttp, voluptuous, and homeassistant stubs."""
    aiohttp_mod = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientResponseError(ClientError):
        def __init__(self, status: int = 500, message: str = "") -> None:
            super().__init__(message or f"HTTP {status}")
            self.status = status
            self.message = message

    class ClientTimeout:
        def __init__(self, total: float | None = None, connect: float | None = None) -> None:
            self.total = total
            self.connect = connect

    class ClientWSTimeout:
        def __init__(self, ws_receive: float | None = None, ws_close: float = 10.0) -> None:
            self.ws_receive = ws_receive
            self.ws_close = ws_close

    class WSMsgType:
        TEXT = 1
        BINARY = 2
        CLOSE = 8
        CLOSED = 257
        ERROR = 258

    class ClientSession:
        pass

    aiohttp_mod.ClientError = ClientError
    aiohttp_mod.ClientResponseError = ClientResponseError
    aiohttp_mod.ClientTimeout = ClientTimeout
    aiohttp_mod.ClientWSTimeout = ClientWSTimeout
    aiohttp_mod.WSMsgType = WSMsgType
    aiohttp_mod.ClientSession = ClientSession
    sys.modules["aiohttp"] = aiohttp_mod

    vol_mod = types.ModuleType("voluptuous")

    class _Marker:
        def __init__(self, schema: Any, default: Any = None) -> None:
            self.schema = schema
            self.default = default

        def __hash__(self) -> int:
            return hash((self.__class__, self.schema))

        def __eq__(self, other: object) -> bool:
            return isinstance(other, _Marker) and self.schema == other.schema

    class Required(_Marker):
        pass

    class Optional(_Marker):
        pass

    class Schema:
        def __init__(self, schema: dict[Any, Any]) -> None:
            self.schema = schema

        def __call__(self, data: Any) -> Any:
            return data

    class All:
        def __init__(self, *validators: Any) -> None:
            self.validators = validators

    class Coerce:
        def __init__(self, target_type: Any) -> None:
            self.target_type = target_type

    class Range:
        def __init__(self, min: Any = None, max: Any = None) -> None:
            self.min = min
            self.max = max

    class In:
        def __init__(self, container: Any) -> None:
            self.container = container

    vol_mod.Required = Required
    vol_mod.Optional = Optional
    vol_mod.Schema = Schema
    vol_mod.All = All
    vol_mod.Coerce = Coerce
    vol_mod.Range = Range
    vol_mod.In = In
    sys.modules["voluptuous"] = vol_mod

    ha_mod = types.ModuleType("homeassistant")
    sys.modules["homeassistant"] = ha_mod

    const_mod = types.ModuleType("homeassistant.const")

    class Platform:
        LIGHT = "light"
        BUTTON = "button"
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        SELECT = "select"
        FAN = "fan"
        NUMBER = "number"
        SWITCH = "switch"

    class EntityCategory:
        DIAGNOSTIC = "diagnostic"
        CONFIG = "config"

    const_mod.Platform = Platform
    const_mod.EntityCategory = EntityCategory
    sys.modules["homeassistant.const"] = const_mod

    core_mod = types.ModuleType("homeassistant.core")

    class HomeAssistant:
        pass

    class ServiceCall:
        def __init__(self, data: dict[str, Any] | None = None) -> None:
            self.data = dict(data or {})

    def callback(func):
        return func

    core_mod.HomeAssistant = HomeAssistant
    core_mod.ServiceCall = ServiceCall
    core_mod.callback = callback
    sys.modules["homeassistant.core"] = core_mod

    exc_mod = types.ModuleType("homeassistant.exceptions")

    class HomeAssistantError(Exception):
        pass

    class ConfigEntryAuthFailed(HomeAssistantError):
        pass

    exc_mod.HomeAssistantError = HomeAssistantError
    exc_mod.ConfigEntryAuthFailed = ConfigEntryAuthFailed
    sys.modules["homeassistant.exceptions"] = exc_mod

    ce_mod = types.ModuleType("homeassistant.config_entries")
    ce_mod.FlowResult = dict

    class ConfigEntry:
        def __init__(
            self,
            entry_id: str = "entry_1",
            title: str = "尼特利鱼缸",
            data: dict[str, Any] | None = None,
            options: dict[str, Any] | None = None,
            version: int = 2,
        ) -> None:
            self.entry_id = entry_id
            self.title = title
            self.data = dict(data or {})
            self.options = dict(options or {})
            self.version = version
            self.runtime_data: Any = None
            self.reauth_started = False
            self.update_listeners: list[Any] = []

        def __class_getitem__(cls, item: Any):
            return cls

        def async_start_reauth(self, hass: Any) -> None:
            self.reauth_started = True

        def add_update_listener(self, listener: Any) -> Any:
            self.update_listeners.append(listener)
            return lambda: None

    class ConfigFlow:
        def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            cls.domain = domain

        def __init__(self) -> None:
            self.hass: Any = None
            self.context: dict[str, Any] = {}

        def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "create_entry", **kwargs}

        def async_abort(self, *, reason: str) -> dict[str, Any]:
            return {"type": "abort", "reason": reason}

        async def async_set_unique_id(self, unique_id: str) -> None:
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            return None

    class OptionsFlow:
        def __init__(self) -> None:
            self._config_entry: ConfigEntry | None = None

        @property
        def config_entry(self) -> ConfigEntry:
            if self._config_entry is None:
                raise RuntimeError("config_entry not attached")
            return self._config_entry

        def async_show_form(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs: Any) -> dict[str, Any]:
            return {"type": "create_entry", **kwargs}

    class OptionsFlowWithReload(OptionsFlow):
        pass

    ce_mod.ConfigEntry = ConfigEntry
    ce_mod.ConfigFlow = ConfigFlow
    ce_mod.OptionsFlow = OptionsFlow
    ce_mod.OptionsFlowWithReload = OptionsFlowWithReload
    sys.modules["homeassistant.config_entries"] = ce_mod
    ha_mod.config_entries = ce_mod

    helpers_mod = types.ModuleType("homeassistant.helpers")
    sys.modules["homeassistant.helpers"] = helpers_mod

    aio_helper_mod = types.ModuleType("homeassistant.helpers.aiohttp_client")
    aio_helper_mod.async_get_clientsession = MagicMock()
    sys.modules["homeassistant.helpers.aiohttp_client"] = aio_helper_mod

    dr_mod = types.ModuleType("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        pass

    dr_mod.DeviceInfo = DeviceInfo
    sys.modules["homeassistant.helpers.device_registry"] = dr_mod

    ep_mod = types.ModuleType("homeassistant.helpers.entity_platform")
    ep_mod.AddEntitiesCallback = Any
    sys.modules["homeassistant.helpers.entity_platform"] = ep_mod

    uc_mod = types.ModuleType("homeassistant.helpers.update_coordinator")

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __init__(
            self,
            hass: Any,
            logger: Any,
            *,
            name: str,
            config_entry: Any = None,
            update_interval: Any = None,
        ) -> None:
            self.hass = hass
            self.logger = logger
            self.name = name
            self.config_entry = config_entry
            self.update_interval = update_interval
            self.data: Any = None
            self.last_update_success = True

        def __class_getitem__(cls, item: Any):
            return cls

        def async_set_updated_data(self, data: Any) -> None:
            self.data = data

        async def async_config_entry_first_refresh(self) -> None:
            self.data = await self._async_update_data()

    class CoordinatorEntity:
        def __init__(self, coordinator: Any) -> None:
            self.coordinator = coordinator

        def __class_getitem__(cls, item: Any):
            return cls

        @property
        def available(self) -> bool:
            return bool(self.coordinator.last_update_success)

    uc_mod.UpdateFailed = UpdateFailed
    uc_mod.DataUpdateCoordinator = DataUpdateCoordinator
    uc_mod.CoordinatorEntity = CoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"] = uc_mod

    util_mod = types.ModuleType("homeassistant.util")
    dt_mod = types.ModuleType("homeassistant.util.dt")
    dt_mod.now = lambda: datetime.now(timezone.utc)
    util_mod.dt = dt_mod
    sys.modules["homeassistant.util"] = util_mod
    sys.modules["homeassistant.util.dt"] = dt_mod

    comp_mod = types.ModuleType("homeassistant.components")
    sys.modules["homeassistant.components"] = comp_mod

    diag_mod = types.ModuleType("homeassistant.components.diagnostics")
    REDACTED = "**REDACTED**"

    def async_redact_data(data: Any, to_redact: set[str]) -> Any:
        if isinstance(data, dict):
            return {
                k: REDACTED if k in to_redact else async_redact_data(v, to_redact)
                for k, v in data.items()
            }
        if isinstance(data, list):
            return [async_redact_data(item, to_redact) for item in data]
        return data

    diag_mod.async_redact_data = async_redact_data
    sys.modules["homeassistant.components.diagnostics"] = diag_mod

    light_mod = types.ModuleType("homeassistant.components.light")
    light_mod.ATTR_BRIGHTNESS = "brightness"
    light_mod.ATTR_EFFECT = "effect"
    light_mod.ATTR_RGBW_COLOR = "rgbw_color"
    light_mod.ColorMode = type("ColorMode", (), {"ONOFF": "onoff", "RGBW": "rgbw", "BRIGHTNESS": "brightness"})
    light_mod.LightEntityFeature = type("LightEntityFeature", (), {"EFFECT": 4})
    light_mod.LightEntity = type("LightEntity", (), {})
    sys.modules["homeassistant.components.light"] = light_mod

    @dataclass(frozen=True, kw_only=True)
    class _BaseDesc:
        key: str
        translation_key: str | None = None
        icon: str | None = None
        entity_category: Any = None
        entity_registry_enabled_default: bool = True
        native_unit_of_measurement: str | None = None

    for plat, ent_cls, desc_cls in (
        ("sensor", "SensorEntity", "SensorEntityDescription"),
        ("binary_sensor", "BinarySensorEntity", "BinarySensorEntityDescription"),
        ("button", "ButtonEntity", "ButtonEntityDescription"),
    ):
        mod = types.ModuleType(f"homeassistant.components.{plat}")
        setattr(mod, ent_cls, type(ent_cls, (), {}))
        setattr(mod, desc_cls, _BaseDesc)
        sys.modules[f"homeassistant.components.{plat}"] = mod

    sel_mod = types.ModuleType("homeassistant.components.select")
    sel_mod.SelectEntity = type("SelectEntity", (), {})
    sys.modules["homeassistant.components.select"] = sel_mod

    fan_mod = types.ModuleType("homeassistant.components.fan")
    fan_mod.FanEntity = type("FanEntity", (), {})
    fan_mod.FanEntityFeature = type("FanEntityFeature", (), {"SET_SPEED": 1, "TURN_ON": 16, "TURN_OFF": 32})
    sys.modules["homeassistant.components.fan"] = fan_mod

    num_mod = types.ModuleType("homeassistant.components.number")
    num_mod.NumberEntity = type("NumberEntity", (), {})
    num_mod.NumberMode = type("NumberMode", (), {"BOX": "box", "SLIDER": "slider"})
    sys.modules["homeassistant.components.number"] = num_mod

    sw_mod = types.ModuleType("homeassistant.components.switch")
    sw_mod.SwitchEntity = type("SwitchEntity", (), {})
    sys.modules["homeassistant.components.switch"] = sw_mod


_install_stubs()

import aiohttp  # noqa: E402
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload  # noqa: E402
from homeassistant.const import EntityCategory  # noqa: E402
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402

import custom_components.netlea_aquarium as netlea_init  # noqa: E402
from custom_components.netlea_aquarium import binary_sensor, button, const, light, protocol, sensor  # noqa: E402
from custom_components.netlea_aquarium.client import (  # noqa: E402
    NetleaAuthError,
    NetleaClient,
    NetleaConnectionError,
    NetleaError,
    is_valid_api_base,
    is_valid_ws_url,
    normalize_legacy_ws_url,
)
from custom_components.netlea_aquarium.config_flow import NetleaConfigFlow, NetleaOptionsFlow  # noqa: E402
from custom_components.netlea_aquarium.coordinator import NetleaCoordinator  # noqa: E402
from custom_components.netlea_aquarium.diagnostics import async_get_config_entry_diagnostics  # noqa: E402


def _load_legacy_api_module() -> types.ModuleType | None:
    """Load the archived legacy api.py for direct byte-for-byte comparison."""
    if not LEGACY_API_PATH.exists():
        return None
    pkg_name = "_legacy_netlea_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(LEGACY_API_PATH.parent)]
    sys.modules[pkg_name] = pkg

    const_spec = importlib.util.spec_from_file_location(f"{pkg_name}.const", LEGACY_API_PATH.parent / "const.py")
    assert const_spec and const_spec.loader
    legacy_const = importlib.util.module_from_spec(const_spec)
    sys.modules[f"{pkg_name}.const"] = legacy_const
    const_spec.loader.exec_module(legacy_const)

    api_spec = importlib.util.spec_from_file_location(f"{pkg_name}.api", LEGACY_API_PATH)
    assert api_spec and api_spec.loader
    legacy_api = importlib.util.module_from_spec(api_spec)
    sys.modules[f"{pkg_name}.api"] = legacy_api
    api_spec.loader.exec_module(legacy_api)
    return legacy_api


class TestNetleaProtocol(unittest.TestCase):
    """Pure protocol tests including byte-for-byte equivalence with legacy api.py."""

    def test_protocol_module_has_zero_ha_or_io_imports(self) -> None:
        source = (REPO_ROOT / "custom_components" / "netlea_aquarium" / "protocol.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"homeassistant", "aiohttp", "socket", "urllib", "http", "threading", "asyncio"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], forbidden)
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], forbidden)

    def test_byte_for_byte_equivalence_with_legacy_api(self) -> None:
        legacy = _load_legacy_api_module()
        # Verified golden frames: legacy mode (20 min, raw F=3) vs real-hardware v2 defaults (60 min, PWM F=145)
        expected_legacy_on = "5A13011A000014001400191421390301090044"
        expected_legacy_off = "5A13011A0000140014000000000000000000B0"
        expected_resume_on = "5A17011000000100000000000000000000000000000083"
        expected_resume_off = "5A17011000000000000000000000000000000000000082"

        self.assertEqual(
            protocol.build_temp_light_frame(True, on_seconds=20, off_seconds=20, fan=3, enforce_safety=False),
            expected_legacy_on,
        )
        self.assertEqual(
            protocol.build_temp_light_frame(False, on_seconds=20, off_seconds=20),
            expected_legacy_off,
        )
        # v2 default: 60 min (0x003C) and fan gear 3 -> hardware PWM 145 (0x91)
        default_v2 = bytes.fromhex(protocol.build_temp_light_frame(True))
        self.assertEqual(default_v2[6] | (default_v2[7] << 8), 60)
        self.assertEqual(default_v2[14], 145)

        active_now = datetime(2026, 11, 19, 14, 30, tzinfo=timezone.utc)  # Thursday (weekday=3)
        inactive_now = datetime(2026, 11, 19, 23, 30, tzinfo=timezone.utc)
        device_with_trip = {
            "statusLog": {
                "haveTravel": {"main": True},
                "travelRunning": {"main": False},
                "onoff": 0,
                "masterhand": {
                    "travelList": [
                        {
                            "name": "水草主时段",
                            "enable": 1,
                            "dayList": [1, 1, 1, 1, 1, 1, 1],
                            "startH": 12,
                            "startM": 0,
                            "endH": 20,
                            "endM": 0,
                        }
                    ]
                },
            }
        }
        self.assertEqual(protocol.build_resume_schedule_frame(device_with_trip, active_now), expected_resume_on)
        self.assertEqual(
            protocol.build_resume_schedule_frame(device_with_trip["statusLog"], active_now), expected_resume_on
        )
        self.assertEqual(protocol.build_resume_schedule_frame(device_with_trip, inactive_now), expected_resume_off)
        self.assertEqual(protocol.build_resume_schedule_frame({"onoff": 1}, inactive_now), expected_resume_on)
        self.assertEqual(protocol.build_resume_schedule_frame({"onoff": 0}, inactive_now), expected_resume_off)

        # Build a valid 0x14 reply frame (real hardware order: R, W, G, B, F)
        reply_payload = [
            1, 12, 0, 1, 1, 3, 25, 20, 33, 57, 145, 0, 0,  # master (13 bytes: R=25, W=20, G=33, B=57, F=145)
            2, 13, 15, 0, 0, 2, 10, 1, 0,  # spot (9 bytes)
            3, 18, 30, 1, 2, 2, 70, 2, 0,  # atmosphere (9 bytes)
        ]
        reply_hex = protocol.build_app_frame(0x01, 0x14, reply_payload)
        decoded = protocol.decode_lamps_state_reply(reply_hex)
        assert decoded is not None
        self.assertEqual(decoded["master"]["lampState"], 1)
        self.assertEqual(decoded["master"]["pattern"], 3)
        self.assertEqual(decoded["master"]["R"], 25)
        self.assertEqual(decoded["master"]["W"], 20)
        self.assertEqual(decoded["master"]["G"], 33)
        self.assertEqual(decoded["master"]["B"], 57)
        self.assertEqual(decoded["master"]["F"], 145)
        self.assertEqual(decoded["spot"]["moodLamp"], 10)
        self.assertEqual(decoded["atmosphere"]["followType"], 2)

        # Cross-check directly against archived legacy api.py when available
        if legacy is not None:
            self.assertEqual(protocol.build_temp_light_frame(True), legacy.legacy_temporary_main_frame(on=True))
            self.assertEqual(protocol.build_temp_light_frame(False), legacy.legacy_temporary_main_frame(on=False))
            self.assertEqual(
                protocol.decode_lamps_state_reply(reply_hex),
                legacy.decode_legacy_lamps_state_reply(reply_hex),
            )
            with patch.object(dt_util, "now", return_value=active_now):
                self.assertEqual(
                    protocol.build_resume_schedule_frame(device_with_trip, active_now),
                    legacy.legacy_resume_schedule_frame(device_with_trip),
                )
            with patch.object(dt_util, "now", return_value=inactive_now):
                self.assertEqual(
                    protocol.build_resume_schedule_frame(device_with_trip, inactive_now),
                    legacy.legacy_resume_schedule_frame(device_with_trip),
                )

    def test_custom_temp_light_channel_options_alter_payload_accurately(self) -> None:
        custom_frame = protocol.build_temp_light_frame(True, red=40, white=30, green=20, blue=45, fan=5)
        raw = bytes.fromhex(custom_frame)
        self.assertEqual(len(raw), 19)
        self.assertEqual(raw[0], 0x5A)
        self.assertEqual(raw[1], 19)
        self.assertEqual(raw[2], 0x01)
        self.assertEqual(raw[3], 0x1A)
        self.assertEqual(list(raw[10:15]), [40, 30, 20, 45, 180])  # gear 5 -> PWM 180
        self.assertEqual(raw[-1], protocol.crc(list(raw[:-1])))

        # Out-of-range channel values are safely power-capped <=91W and fan mapped cleanly
        clamped_frame = protocol.build_temp_light_frame(True, red=-10, white=150, green=50, blue=200, fan=9)
        clamped = bytes.fromhex(clamped_frame)
        self.assertEqual(clamped[10], 0)
        self.assertEqual(clamped[14], 240)  # gear 9 -> PWM 240
        self.assertEqual(clamped[-1], protocol.crc(list(clamped[:-1])))

        # Off frame always zeros channels regardless of configured defaults
        self.assertEqual(
            protocol.build_temp_light_frame(False, red=80, white=60, green=40, blue=90, fan=5),
            protocol.build_temp_light_frame(False),
        )

    def test_schedule_inference_overnight_window_and_weekday_filter(self) -> None:
        overnight_status = {
            "haveTravel": {"main": True},
            "masterhand": {
                "travelList": [
                    {
                        "name": "夜间观景",
                        "enable": 1,
                        "dayList": [1, 1, 1, 1, 0, 1, 1],  # Friday (weekday=4) disabled
                        "startH": 22,
                        "startM": 0,
                        "endH": 2,
                        "endM": 0,
                    }
                ]
            },
        }
        thu_late = datetime(2026, 11, 19, 23, 15, tzinfo=timezone.utc)  # weekday 3
        fri_late = datetime(2026, 11, 20, 23, 15, tzinfo=timezone.utc)  # weekday 4
        sat_early = datetime(2026, 11, 21, 1, 30, tzinfo=timezone.utc)  # weekday 5
        self.assertIsNotNone(protocol.find_active_main_trip(overnight_status, thu_late))
        self.assertIsNone(protocol.find_active_main_trip(overnight_status, fri_late))
        self.assertIsNotNone(protocol.find_active_main_trip(overnight_status, sat_early))
        self.assertEqual(protocol.infer_resume_lamp_state(overnight_status, thu_late), 1)
        self.assertEqual(protocol.infer_resume_lamp_state(overnight_status, fri_late), 0)


class TestNetleaIntegration(unittest.IsolatedAsyncioTestCase):
    """Client, coordinator, config/options flow, entity, and diagnostics tests."""

    async def test_wss_default_migration_url_validation_and_runtime_data(self) -> None:
        self.assertEqual(const.WS_URL, "wss://v3ws.netlea.com/websocket")
        self.assertEqual(normalize_legacy_ws_url(const.LEGACY_WS_URL), const.WS_URL)
        self.assertTrue(is_valid_api_base(const.API_BASE))
        self.assertFalse(is_valid_api_base("http://v3api.netlea.com/prod-api"))
        self.assertTrue(is_valid_ws_url(const.WS_URL))
        self.assertFalse(is_valid_ws_url("ws://custom.example.com/ws"))

        with self.assertRaises(NetleaError):
            NetleaClient(MagicMock(), api_base="http://v3api.netlea.com/prod-api", ws_url=const.WS_URL)
        with self.assertRaises(NetleaError):
            NetleaClient(MagicMock(), api_base=const.API_BASE, ws_url="ws://custom.example.com/ws")

        entry = ConfigEntry(
            entry_id="e1",
            data={
                const.CONF_TOKEN: "tok",
                const.CONF_USER_ID: "u1",
                const.CONF_EQUIPMENT_USER_ID: "eq1",
                const.CONF_CONTROL_REAL_ADDRESS: "AA11",
                const.CONF_API_BASE: const.API_BASE,
                const.CONF_WS_URL: const.LEGACY_WS_URL,
            },
        )
        hass = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.config_entries.async_update_entry = MagicMock(
            side_effect=lambda target, *, data: setattr(target, "data", dict(data))
        )

        fake_device = {"equipmentUserId": "eq1", "controlRealAddress": "AA11", "name": "草缸", "statusLog": {"onoff": 1}}
        with patch.object(NetleaClient, "async_fetch_devices", new=AsyncMock(return_value=[fake_device])):
            ok = await netlea_init.async_setup_entry(hass, entry)

        self.assertTrue(ok)
        hass.config_entries.async_update_entry.assert_called_once()
        self.assertEqual(entry.data[const.CONF_WS_URL], const.WS_URL)
        self.assertIsInstance(entry.runtime_data, NetleaCoordinator)
        self.assertEqual(entry.runtime_data.client.ws_url, const.WS_URL)
        self.assertEqual(entry.update_listeners, [])
        self.assertTrue(await netlea_init.async_unload_entry(hass, entry))

        # ConfigFlow rejects non-TLS endpoints
        flow = NetleaConfigFlow()
        flow.hass = hass
        result = await flow.async_step_user(
            {
                const.CONF_PHONE: "13800138000",
                const.CONF_API_BASE: "http://v3api.netlea.com/prod-api",
                const.CONF_WS_URL: const.LEGACY_WS_URL,
            }
        )
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"][const.CONF_API_BASE], "invalid_api_base")
        self.assertEqual(result["errors"][const.CONF_WS_URL], "invalid_ws_url")

    async def test_auth_errors_never_leak_tokens_or_raw_responses(self) -> None:
        secret = "SUPER_SECRET_TOKEN_12345"

        class _FakeResp:
            def __init__(self, status: int, body: str) -> None:
                self.status = status
                self._body = body

            async def text(self) -> str:
                return self._body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

        session = MagicMock()
        session.request = MagicMock(return_value=_FakeResp(401, f'{{"msg":"denied {secret}"}}'))
        client = NetleaClient(session, token=secret, user_id="u1")

        with self.assertRaises(NetleaAuthError) as ctx:
            await client.async_fetch_devices()
        self.assertEqual(session.request.call_count, 1)
        self.assertNotIn(secret, str(ctx.exception))

        # Login missing userId must not echo token
        session.request = MagicMock(return_value=_FakeResp(200, json.dumps({"code": 200, "token": secret})))
        with self.assertRaises(NetleaAuthError) as ctx2:
            await client.async_login("13800138000", "1234", "uuid1")
        self.assertNotIn(secret, str(ctx2.exception))

    async def test_ws_timeout_fallback_retry_cap_and_reply_roundtrip(self) -> None:
        ws_calls: list[dict[str, Any]] = []

        class _FailingWSContext:
            async def __aenter__(self):
                raise TimeoutError()

            async def __aexit__(self, *args):
                return None

        session = MagicMock()
        session.ws_connect = lambda url, **kw: (ws_calls.append({"url": url, **kw}) or _FailingWSContext())
        client = NetleaClient(session, token="tok")

        with patch("custom_components.netlea_aquarium.client._sleep_before_retry", new=AsyncMock()):
            with self.assertRaises(NetleaConnectionError) as ctx:
                await client.async_send_frame("AA11", protocol.build_temp_light_frame(True), retries=5)

        self.assertEqual(len(ws_calls), const.DEFAULT_CONTROL_RETRIES)
        self.assertEqual(const.DEFAULT_CONTROL_RETRIES, 2)
        self.assertIn("TimeoutError", str(ctx.exception))
        self.assertIsInstance(ws_calls[0]["timeout"], aiohttp.ClientWSTimeout)
        self.assertEqual(ws_calls[0]["timeout"].ws_receive, const.DEFAULT_CONTROL_REPLY_TIMEOUT)

        # Fallback when ClientWSTimeout is absent
        ws_calls.clear()
        orig = aiohttp.ClientWSTimeout
        delattr(aiohttp, "ClientWSTimeout")
        try:
            with patch("custom_components.netlea_aquarium.client._sleep_before_retry", new=AsyncMock()):
                with self.assertRaises(NetleaConnectionError):
                    await client.async_send_frame("AA11", protocol.build_temp_light_frame(True))
            self.assertEqual(ws_calls[0]["timeout"], const.DEFAULT_CONTROL_CONNECT_TIMEOUT)
        finally:
            aiohttp.ClientWSTimeout = orig

        # Handshake 401 raises NetleaAuthError immediately without retry
        ws_calls.clear()

        class _AuthFailWSContext:
            async def __aenter__(self):
                raise aiohttp.ClientResponseError(status=401)

            async def __aexit__(self, *args):
                return None

        session.ws_connect = lambda url, **kw: (ws_calls.append({"url": url, **kw}) or _AuthFailWSContext())
        with self.assertRaises(NetleaAuthError):
            await client.async_send_frame("AA11", protocol.build_temp_light_frame(True))
        self.assertEqual(len(ws_calls), 1)

        # Successful WS control round-trip with 0x14 reply decoding
        reply_frame = protocol.build_app_frame(
            0x01, 0x14, [1, 12, 0, 1, 1, 3, 80, 40, 60, 90, 5, 0, 0] + [0] * 18
        )
        sent_messages: list[str] = []

        class _GoodWS:
            async def send_str(self, msg: str) -> None:
                sent_messages.append(msg)

            async def receive(self, timeout: float | None = None):
                return types.SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps({"msgBody": {"realAddress": "AA11", "message": reply_frame}}),
                )

        class _GoodWSCtx:
            async def __aenter__(self):
                return _GoodWS()

            async def __aexit__(self, *args):
                return None

        session.ws_connect = lambda url, **kw: _GoodWSCtx()
        res = await client.async_temp_on("AA11", red=80, white=60, green=40, blue=90, fan=5)
        self.assertEqual(res.replied_addresses, ["AA11"])
        self.assertEqual(res.missing_addresses, [])
        self.assertEqual(res.legacy_state["master"]["lampState"], 1)
        sent_payload = json.loads(sent_messages[0])
        self.assertEqual(sent_payload["msgBody"]["message"], protocol.build_temp_light_frame(True, 80, 60, 40, 90, 5))

    async def test_stale_grace_period_and_auth_polling(self) -> None:
        entry = ConfigEntry(
            entry_id="e1",
            data={const.CONF_EQUIPMENT_USER_ID: "eq1", const.CONF_CONTROL_REAL_ADDRESS: "AA11"},
            options={const.CONF_STALE_FAILURES: 2},
        )
        client = MagicMock()
        client.async_fetch_devices = AsyncMock(
            return_value=[{"equipmentUserId": "eq1", "controlRealAddress": "AA11", "name": "草缸", "statusLog": {"onoff": 1}}]
        )
        coordinator = NetleaCoordinator(MagicMock(), client, entry)
        await coordinator.async_config_entry_first_refresh()
        lamp = light.NetleaAquariumLight(coordinator)
        self.assertTrue(lamp.available)
        self.assertFalse(lamp.extra_state_attributes["stale"])

        client.async_fetch_devices.side_effect = NetleaConnectionError("timeout")
        coordinator.data = await coordinator._async_update_data()
        self.assertTrue(lamp.available)
        self.assertTrue(lamp.extra_state_attributes["stale"])

        coordinator.data = await coordinator._async_update_data()
        self.assertTrue(lamp.available)

        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()
        coordinator.last_update_success = False
        self.assertFalse(lamp.available)

        client.async_fetch_devices.side_effect = NetleaAuthError("expired")
        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_options_flow_with_reload_custom_channels_and_translations(self) -> None:
        entry = ConfigEntry(
            entry_id="e1",
            data={const.CONF_EQUIPMENT_USER_ID: "eq1", const.CONF_CONTROL_REAL_ADDRESS: "AA11"},
            options={
                const.CONF_SCAN_INTERVAL: 120,
                const.CONF_STALE_FAILURES: 4,
                const.CONF_TEMP_RED: 70,
                const.CONF_TEMP_WHITE: 50,
                const.CONF_TEMP_GREEN: 40,
                const.CONF_TEMP_BLUE: 80,
                const.CONF_TEMP_FAN: 4,
            },
        )
        flow = NetleaConfigFlow.async_get_options_flow(entry)
        self.assertIsInstance(flow, NetleaOptionsFlow)
        self.assertIsInstance(flow, OptionsFlowWithReload)
        self.assertNotIn("__init__", NetleaOptionsFlow.__dict__)
        with self.assertRaises(TypeError):
            NetleaOptionsFlow(entry)  # type: ignore[call-arg]

        flow._config_entry = entry
        form = await flow.async_step_init()
        self.assertEqual(form["type"], "form")
        self.assertEqual(form["step_id"], "init")

        clamped = await flow.async_step_init(
            {
                const.CONF_SCAN_INTERVAL: 5,
                const.CONF_STALE_FAILURES: 99,
                const.CONF_TEMP_RED: -5,
                const.CONF_TEMP_WHITE: 120,
                const.CONF_TEMP_GREEN: 33,
                const.CONF_TEMP_BLUE: 57,
                const.CONF_TEMP_FAN: 9,
            }
        )
        self.assertEqual(
            clamped["data"],
            {
                const.CONF_SCAN_INTERVAL: 15,
                const.CONF_STALE_FAILURES: 5,
                const.CONF_TEMP_ON_MINUTES: 60,
                const.CONF_TEMP_RED: 0,
                const.CONF_TEMP_WHITE: 120,
                const.CONF_TEMP_GREEN: 33,
                const.CONF_TEMP_BLUE: 57,
                const.CONF_TEMP_FAN: 9,
            },
        )

        # Coordinator applies options and forwards custom channels to client.async_temp_on
        client = MagicMock()
        client.async_temp_on = AsyncMock(
            return_value=protocol.ControlResult(
                frame=protocol.build_temp_light_frame(True, 70, 50, 40, 80, 4),
                replies=[{"msgBody": {"realAddress": "AA11"}}],
                expected_addresses=["AA11"],
                replied_addresses=["AA11"],
                missing_addresses=[],
                legacy_state={"master": {"lampState": 1, "pattern": 3}},
            )
        )
        coordinator = NetleaCoordinator(MagicMock(), client, entry)
        coordinator.data = {"controlRealAddress": "AA11", "statusLog": {"onoff": 0}}
        self.assertEqual(coordinator.update_interval, timedelta(seconds=120))
        self.assertEqual(coordinator.stale_failures, 4)

        await coordinator.async_temp_on()
        client.async_temp_on.assert_awaited_once_with("AA11", red=70, white=50, green=40, blue=80, fan=4)

        # Translations key parity & version 2.0.0
        base = REPO_ROOT / "custom_components" / "netlea_aquarium"
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "2.0.1")
        strings = json.loads((base / "strings.json").read_text(encoding="utf-8"))
        en = json.loads((base / "translations" / "en.json").read_text(encoding="utf-8"))
        zh = json.loads((base / "translations" / "zh-Hans.json").read_text(encoding="utf-8"))

        def key_tree(obj: Any) -> Any:
            return {k: key_tree(v) for k, v in obj.items()} if isinstance(obj, dict) else True

        self.assertEqual(key_tree(strings), key_tree(en))
        self.assertEqual(key_tree(strings), key_tree(zh))
        for opt_key in (
            const.CONF_SCAN_INTERVAL,
            const.CONF_STALE_FAILURES,
            const.CONF_TEMP_RED,
            const.CONF_TEMP_WHITE,
            const.CONF_TEMP_GREEN,
            const.CONF_TEMP_BLUE,
            const.CONF_TEMP_FAN,
        ):
            self.assertIn(opt_key, strings["options"]["step"]["init"]["data"])

    async def test_control_auth_error_triggers_reauth_and_reauth_flow(self) -> None:
        entry = ConfigEntry(
            entry_id="e1",
            data={
                const.CONF_PHONE: "13800138000",
                const.CONF_EQUIPMENT_USER_ID: "eq1",
                const.CONF_CONTROL_REAL_ADDRESS: "AA11",
                const.CONF_API_BASE: const.API_BASE,
                const.CONF_WS_URL: const.LEGACY_WS_URL,
            },
        )
        hass = MagicMock()
        hass.config_entries.async_get_entry = MagicMock(return_value=entry)
        hass.config_entries.async_update_entry = MagicMock()
        hass.config_entries.async_reload = AsyncMock()

        client = MagicMock()
        client.async_temp_on = AsyncMock(side_effect=NetleaAuthError("expired"))
        coordinator = NetleaCoordinator(hass, client, entry)
        coordinator.data = {"controlRealAddress": "AA11", "statusLog": {"onoff": 0}}

        with self.assertRaises(HomeAssistantError):
            await coordinator.async_temp_on()
        self.assertTrue(entry.reauth_started)

        flow = NetleaConfigFlow()
        flow.hass = hass
        flow.context = {"entry_id": "e1"}
        step1 = await flow.async_step_reauth(entry.data)
        self.assertEqual(step1["step_id"], "reauth_confirm")
        self.assertEqual(flow._ws_url, const.WS_URL)

        with patch.object(NetleaClient, "async_send_code", new=AsyncMock(return_value="uuid_new")):
            step2 = await flow.async_step_reauth_confirm({const.CONF_PHONE: "13800138000"})
        self.assertEqual(step2["step_id"], "reauth_code")

        login_result = {
            "token": "tok_new",
            "user_id": "u1",
            "devices": [{"equipmentUserId": "eq1", "controlRealAddress": "AA11", "name": "草缸"}],
        }
        with patch.object(NetleaClient, "async_login", new=AsyncMock(return_value=login_result)):
            step3 = await flow.async_step_reauth_code({const.CONF_CODE: "654321"})
        self.assertEqual(step3, {"type": "abort", "reason": "reauth_successful"})
        updated_data = hass.config_entries.async_update_entry.call_args.kwargs["data"]
        self.assertEqual(updated_data[const.CONF_TOKEN], "tok_new")
        self.assertEqual(updated_data[const.CONF_WS_URL], const.WS_URL)

    async def test_all_11_unique_ids_lean_light_attrs_recorder_hygiene_and_diagnostics(self) -> None:
        eid = "eq-live-99"
        entry = ConfigEntry(
            entry_id="e1",
            title="草缸",
            data={
                const.CONF_PHONE: "13800138000",
                const.CONF_UUID: "uuid-secret",
                const.CONF_TOKEN: "token-secret",
                const.CONF_USER_ID: "user-secret",
                const.CONF_EQUIPMENT_USER_ID: eid,
                const.CONF_CONTROL_REAL_ADDRESS: "AABBCC,DDEEFF",
                const.CONF_DEVICE_NAME: "草缸",
                const.CONF_API_BASE: const.API_BASE,
                const.CONF_WS_URL: const.WS_URL,
            },
            options={const.CONF_SCAN_INTERVAL: 60, const.CONF_STALE_FAILURES: 2},
        )
        coordinator = NetleaCoordinator(MagicMock(), MagicMock(), entry)
        coordinator.last_control = {
            "action": const.ACTION_TEMP_ON,
            "frame": protocol.build_temp_light_frame(True),
            "reply_count": 2,
            "expected_addresses": ["AABBCC", "DDEEFF"],
            "replied_addresses": ["AABBCC"],
            "missing_addresses": ["DDEEFF"],
            "legacy_state": {"master": {"lampState": 1, "pattern": 3}},
            "time": "2026-11-19T12:00:00+00:00",
        }
        coordinator.data = {
            "name": "草缸",
            "equipmentUserId": eid,
            "controlRealAddress": "AABBCC,DDEEFF",
            "_stale": False,
            "_last_control": coordinator.last_control,
            "_last_update_error": "none",
            "statusLog": {"onoff": 1, "onLineRealAddress": "AABBCC,DDEEFF", "haveTravel": {"main": True}},
        }
        entry.runtime_data = coordinator

        collected: list[Any] = []
        hass = MagicMock()
        await light.async_setup_entry(hass, entry, collected.extend)
        await button.async_setup_entry(hass, entry, collected.extend)
        await binary_sensor.async_setup_entry(hass, entry, collected.extend)
        await sensor.async_setup_entry(hass, entry, collected.extend)

        unique_ids = {ent._attr_unique_id for ent in collected}
        expected_ids = {
            f"{eid}_light",
            f"{eid}_temp_on",
            f"{eid}_temp_off",
            f"{eid}_resume_schedule",
            f"{eid}_has_travel",
            f"{eid}_travel_running",
            f"{eid}_run_mode",
            f"{eid}_current_trip",
            f"{eid}_online_count",
            f"{eid}_reply_count",
            f"{eid}_last_command",
        }
        self.assertGreaterEqual(len(collected), 11)
        self.assertTrue(expected_ids.issubset(unique_ids))

        # Light entity: no _attr_name, proper translation_key, core attrs preserved & no sensitive/removed keys
        lamp = next(e for e in collected if isinstance(e, light.NetleaAquariumLight))
        self.assertNotIn("_attr_name", light.NetleaAquariumLight.__dict__)
        self.assertEqual(lamp._attr_translation_key, "aquarium_light")
        self.assertTrue(lamp._attr_has_entity_name)
        self.assertTrue({"mode", "current_trip", "stale"}.issubset(set(lamp.extra_state_attributes.keys())))
        for removed in (
            "control_real_address",
            "expected_addresses",
            "replied_addresses",
            "missing_addresses",
            "last_reply_count",
            "last_command",
            "last_frame",
            "last_update_error",
        ):
            self.assertNotIn(removed, lamp.extra_state_attributes)

        # Sensor Recorder hygiene: reply_count & last_command disabled by default + diagnostic
        sensors_by_key = {e.entity_description.key: e for e in collected if isinstance(e, sensor.NetleaSensor)}
        self.assertTrue(sensors_by_key["run_mode"].entity_description.entity_registry_enabled_default)
        self.assertTrue(sensors_by_key["current_trip"].entity_description.entity_registry_enabled_default)
        self.assertTrue(sensors_by_key["online_count"].entity_description.entity_registry_enabled_default)
        self.assertEqual(sensors_by_key["online_count"].native_value, 2)
        for debug_key in ("reply_count", "last_command"):
            desc = sensors_by_key[debug_key].entity_description
            self.assertEqual(desc.entity_category, EntityCategory.DIAGNOSTIC)
            self.assertFalse(desc.entity_registry_enabled_default)

        # Diagnostics redaction + safe diagnostic telemetry
        diag = await async_get_config_entry_diagnostics(hass, entry)
        self.assertEqual(diag["entry"]["data"][const.CONF_TOKEN], "**REDACTED**")
        self.assertEqual(diag["entry"]["data"][const.CONF_PHONE], "**REDACTED**")
        self.assertEqual(diag["entry"]["data"][const.CONF_UUID], "**REDACTED**")
        self.assertEqual(diag["entry"]["data"][const.CONF_USER_ID], "**REDACTED**")
        self.assertEqual(diag["entry"]["data"][const.CONF_EQUIPMENT_USER_ID], "**REDACTED**")
        self.assertEqual(diag["entry"]["data"][const.CONF_CONTROL_REAL_ADDRESS], "**REDACTED**")
        self.assertEqual(diag["coordinator"]["data"]["controlRealAddress"], "**REDACTED**")
        self.assertEqual(diag["coordinator"]["data"]["statusLog"]["onLineRealAddress"], "**REDACTED**")
        self.assertEqual(diag["coordinator"]["last_control"]["expected_addresses"], "**REDACTED**")
        self.assertEqual(diag["coordinator"]["last_control"]["replied_addresses"], "**REDACTED**")
        self.assertEqual(diag["coordinator"]["last_control"]["missing_addresses"], "**REDACTED**")
        self.assertEqual(diag["coordinator"]["last_control"]["expected_address_count"], 2)
        self.assertEqual(diag["coordinator"]["last_control"]["replied_address_count"], 1)
        self.assertEqual(diag["coordinator"]["last_control"]["missing_address_count"], 1)
        self.assertEqual(diag["coordinator"]["last_control"]["reply_count"], 2)
        self.assertEqual(diag["coordinator"]["temp_channels"], {"red": 25, "white": 20, "green": 33, "blue": 57, "fan": 3})

        # Button actions dispatch to coordinator methods cleanly
        coordinator.async_temp_on = AsyncMock()
        coordinator.async_temp_off = AsyncMock()
        coordinator.async_resume_schedule = AsyncMock()
        buttons_by_key = {e.entity_description.key: e for e in collected if isinstance(e, button.NetleaActionButton)}
        await buttons_by_key[const.ACTION_TEMP_ON].async_press()
        await buttons_by_key[const.ACTION_TEMP_OFF].async_press()
        await buttons_by_key[const.ACTION_RESUME_SCHEDULE].async_press()
        coordinator.async_temp_on.assert_awaited_once()
        coordinator.async_temp_off.assert_awaited_once()
        coordinator.async_resume_schedule.assert_awaited_once()

        # Binary sensors reflect statusLog accurately
        b_by_key = {e.entity_description.key: e for e in collected if isinstance(e, binary_sensor.NetleaBinarySensor)}
        self.assertTrue(b_by_key["has_travel"].is_on)
        self.assertFalse(b_by_key["travel_running"].is_on)

    async def test_user_code_device_multi_device_setup_flow(self) -> None:
        hass = MagicMock()
        flow = NetleaConfigFlow()
        flow.hass = hass
        with patch.object(NetleaClient, "async_send_code", new=AsyncMock(return_value="uuid-1")):
            step_code = await flow.async_step_user({const.CONF_PHONE: "+86 138-0013-8000"})
        self.assertEqual(step_code["step_id"], "code")
        self.assertEqual(flow._phone, "13800138000")

        devices = [
            {"equipmentUserId": "eq1", "controlRealAddress": "AA11", "name": "客厅草缸", "shareFlag": 1},
            {"equipmentUserId": "eq2", "controlRealAddress": "BB22", "name": "书房虾缸", "shareFlag": 2},
        ]
        with patch.object(
            NetleaClient,
            "async_login",
            new=AsyncMock(return_value={"token": "tok1", "user_id": "u1", "devices": devices}),
        ):
            step_dev = await flow.async_step_code({const.CONF_CODE: "123456"})
        self.assertEqual(step_dev["step_id"], "device")

        created = await flow.async_step_device({"device": "eq2"})
        self.assertEqual(created["type"], "create_entry")
        self.assertEqual(created["title"], "书房虾缸")
        self.assertEqual(created["data"][const.CONF_EQUIPMENT_USER_ID], "eq2")
        self.assertEqual(created["data"][const.CONF_CONTROL_REAL_ADDRESS], "BB22")
        self.assertEqual(created["data"][const.CONF_WS_URL], const.WS_URL)
        self.assertEqual(created["data"][const.CONF_API_BASE], const.API_BASE)


if __name__ == "__main__":
    unittest.main()
