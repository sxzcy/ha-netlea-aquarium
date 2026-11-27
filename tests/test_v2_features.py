"""Comprehensive v2.0.0 tests: full codecs, safety limits, Local MQTT server E2E, BLE mock, Option C, migration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import struct
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock

# Ensure stubs from test_netlea_rewrite are installed first
import test_netlea_rewrite as _base  # noqa: E402, F401

from homeassistant.config_entries import ConfigEntry  # noqa: E402

import custom_components.netlea_aquarium as netlea_init  # noqa: E402
from custom_components.netlea_aquarium import (  # noqa: E402
    const,
    fan,
    light,
    number,
    presets,
    protocol,
    select,
    sensor,
    switch,
)
from custom_components.netlea_aquarium.config_flow import NetleaConfigFlow  # noqa: E402
from custom_components.netlea_aquarium.coordinator import NetleaCoordinator  # noqa: E402
from custom_components.netlea_aquarium.diagnostics import async_get_config_entry_diagnostics  # noqa: E402
from custom_components.netlea_aquarium.transport import (  # noqa: E402
    LocalMqttServerTransport,
    decrypt_mqtt_frame,
    encrypt_mqtt_frame,
)

TEST_AES_KEY_HEX = "00112233445566778899AABBCCDDEEFF"
TEST_AES_KEY = bytes.fromhex(TEST_AES_KEY_HEX)


def _mqtt_connect_pkt(client_id: str, username: str = "netlea-01-01", password: str = "0" * 40) -> bytes:
    body = bytearray(b"\x00\x04MQTT\x04\xC2\x00\x78")
    for s in (client_id, username, password):
        sb = s.encode("utf-8")
        body += struct.pack("!H", len(sb)) + sb
    return bytes([0x10, len(body)]) + bytes(body)


def _mqtt_subscribe_pkt(topic: str, pid: int = 1) -> bytes:
    tb = topic.encode("utf-8")
    body = struct.pack("!H", pid) + struct.pack("!H", len(tb)) + tb + b"\x00"
    return bytes([0x82, len(body)]) + body


def _mqtt_publish_pkt(topic: str, payload: bytes) -> bytes:
    tb = topic.encode("utf-8")
    body = struct.pack("!H", len(tb)) + tb + payload
    # Encode varint len
    rem = bytearray()
    n = len(body)
    while True:
        d = n % 128
        n //= 128
        if n > 0:
            d |= 0x80
        rem.append(d)
        if n == 0:
            break
    return b"\x30" + bytes(rem) + body


class TestNetleaV2ProtocolAndSafety(unittest.TestCase):
    """Test real-hardware verified frames, presets, and thermal/power safety clamps."""

    def test_real_hardware_0x14_channel_order_rwgbf(self) -> None:
        # Exact frame captured from real NL-N7-90-T32 (08927278CCE6) in moonlight mode
        real_moonlight = "5A26011400000117000100020003000300000001000000000100000001000001000146010002"
        decoded = protocol.decode_lamps_state_reply(real_moonlight)
        assert decoded is not None
        m = decoded["master"]
        self.assertEqual((m["R"], m["W"], m["G"], m["B"], m["F"]), (0, 3, 0, 3, 0))
        self.assertEqual(m["pattern"], 2)
        self.assertEqual(decoded["atmosphere"]["moodLamp"], 0x46)  # 70 = gear 3
        self.assertEqual(decoded["atmosphere"]["followType"], 1)

    def test_real_hardware_0x0a_heartbeat_decode(self) -> None:
        hb = protocol.decode_heartbeat_0x0a("5A0B010A00D26F000000B1")
        assert hb is not None
        self.assertEqual(hb["rssi"], -46)
        self.assertEqual(hb["firmware_version"], "0000006F")

    def test_real_hardware_0x12_schedule_roundtrip(self) -> None:
        real_0x12 = (
            "5A98011200000100000000017F0D0017002E243863B4011000017F17000D000003000300000200"
            + "00" * 112
            + "08"
        )
        decoded = protocol.decode_schedule_0x12(real_0x12, "NL-N7-90-T32")
        assert decoded is not None
        self.assertTrue(decoded["enabled"])
        self.assertEqual(len(decoded["trips"]), 2)
        self.assertIn("原生-溪流", decoded["trips"][0]["name"])
        self.assertIn("自然-月光", decoded["trips"][1]["name"])
        rebuilt = protocol.build_schedule_frame_0x12(decoded["trips"])
        self.assertEqual(rebuilt, real_0x12)

    def test_sync_time_0x04_west_timezone_and_crc(self) -> None:
        dt = datetime(2026, 11, 27, 13, 45, 30, tzinfo=timezone.utc)
        f_west = protocol.build_sync_time_0x04(dt, tz_offset_hours=-5, group_id=7, lamp_index=2)
        raw = bytes.fromhex(f_west)
        self.assertTrue(protocol.verify_frame_bytes(raw))
        self.assertEqual(len(raw), 16)
        self.assertEqual(raw[3], 0x04)
        self.assertEqual(list(raw[6:15]), [7, 2, 128 + 5, 26, 11, 27, 13, 45, 30])

        # Also verify 0x10 manual, 0x1E atmosphere manual, 0x22 follow, and 0x06 token lengths match spec
        m10 = bytes.fromhex(protocol.build_manual_light_frame(True, 46, 36, 56, 99, 180))
        self.assertEqual(len(m10), 23)
        self.assertEqual(m10[6:15], bytes([1, 1, 0, 0, 46, 36, 56, 99, 180]))
        a1e = bytes.fromhex(protocol.build_atmosphere_manual_0x1e(True, 70, 3))
        self.assertEqual(len(a1e), 16)
        self.assertEqual(a1e[6:15], bytes([1, 1, 0, 0, 70, 0xE8, 0x03, 3, 0]))
        a22 = bytes.fromhex(protocol.build_atmosphere_follow_0x22(1, 70, 3))
        self.assertEqual(len(a22), 10)
        self.assertEqual(list(a22[6:9]), [1, 70, 3])
        t06 = bytes.fromhex(protocol.build_ble_token_0x06(1, 0))
        self.assertEqual(len(t06), 12)

    def test_power_cap_and_fan_thermal_safety(self) -> None:
        # Attempting full blast 255 on all 4 channels with fan=0 MUST scale down <=91W and force fan >=120
        r, w, g, b, f_pwm = presets.enforce_power_and_fan_safety(255, 255, 255, 255, 0)
        self.assertLessEqual(presets.estimate_power_w(r, w, g, b, f_pwm), presets.MAX_TOTAL_POWER_W)
        self.assertGreaterEqual(f_pwm, presets.MIN_SAFE_FAN_PWM)

        # Moonlight low power (<=20W) allows fan=0
        r2, w2, g2, b2, f2 = presets.enforce_power_and_fan_safety(0, 3, 0, 3, 0)
        self.assertEqual((r2, w2, g2, b2, f2), (0, 3, 0, 3, 0))

        # Fan gear mapping exactness
        self.assertEqual(presets.fan_gear_to_pwm(0), 0)
        self.assertEqual(presets.fan_gear_to_pwm(3), 145)
        self.assertEqual(presets.fan_gear_to_pwm(5), 180)
        self.assertEqual(presets.fan_pwm_to_gear(145), 3)
        self.assertEqual(presets.fan_pwm_to_gear(180), 5)

    def test_aes_128_ecb_zero_padded_roundtrip(self) -> None:
        frame = protocol.build_query_state_frame()
        ct = encrypt_mqtt_frame(frame, TEST_AES_KEY)
        self.assertEqual(len(ct), 16)
        pt = decrypt_mqtt_frame(ct, TEST_AES_KEY)
        self.assertEqual(pt, frame)


class TestNetleaV2EndToEnd(unittest.IsolatedAsyncioTestCase):
    """End-to-end tests for embedded Local MQTT server, Option C entities, migration, and diagnostics."""

    async def test_local_mqtt_server_whitelist_heartbeat_and_command_roundtrip(self) -> None:
        mac = "08927278CCE6"
        transport = LocalMqttServerTransport(
            allowed_addresses=[mac],
            aes_key_hex=TEST_AES_KEY_HEX,
            bind_host="127.0.0.1",
            port=0,  # OS assigns ephemeral free port
        )
        await transport.async_start()
        try:
            port = transport.bound_port

            # 1) Unauthorized client must be rejected with CONNACK 0x05
            r_bad, w_bad = await asyncio.open_connection("127.0.0.1", port)
            w_bad.write(_mqtt_connect_pkt("DEADBEEF0000"))
            await w_bad.drain()
            connack_bad = await asyncio.wait_for(r_bad.readexactly(4), timeout=2.0)
            self.assertEqual(connack_bad, b"\x20\x02\x00\x05")
            w_bad.close()

            # 2) Authorized lamp connects, subscribes, pushes encrypted 0x0A heartbeat & responds to 0x15
            entry = ConfigEntry(
                entry_id="e_mqtt",
                title="草缸三灯",
                data={
                    const.CONF_TRANSPORT_MODE: const.TRANSPORT_LOCAL_MQTT,
                    const.CONF_EQUIPMENT_USER_ID: "428560",
                    const.CONF_CONTROL_REAL_ADDRESS: mac,
                    const.CONF_MODEL: "NL-N7-90-T32",
                    const.CONF_MQTT_AES_KEY: TEST_AES_KEY_HEX,
                },
            )
            coordinator = NetleaCoordinator(MagicMock(), transport, entry)

            r_lamp, w_lamp = await asyncio.open_connection("127.0.0.1", port)
            try:
                w_lamp.write(_mqtt_connect_pkt(mac))
                await w_lamp.drain()
                self.assertEqual(await asyncio.wait_for(r_lamp.readexactly(4), timeout=2.0), b"\x20\x02\x00\x00")

                w_lamp.write(_mqtt_subscribe_pkt(f"App/Control/{mac}", pid=7))
                await w_lamp.drain()
                suback = await asyncio.wait_for(r_lamp.readexactly(5), timeout=2.0)
                self.assertEqual(suback[0], 0x90)

                # Push encrypted 0x0A heartbeat
                hb_ct = encrypt_mqtt_frame("5A0B010A00D26F000000B1", TEST_AES_KEY)
                w_lamp.write(_mqtt_publish_pkt(f"Device/Heartbeat/{mac}", hb_ct))
                await w_lamp.drain()
                await asyncio.sleep(0.1)

                self.assertEqual(coordinator.data["statusLog"]["_wifi_rssi"], -46)
                self.assertEqual(coordinator.data["statusLog"]["_firmware_version"], "0000006F")
                self.assertEqual(transport.connected_addresses(), [mac])

                # Simulate lamp responding to incoming command on App/Control/<MAC>
                reply_0x14 = "5A2601140000010D000100022E243863B40000010000000001000000010000010001460100D5"
                # Recompute valid CRC for our test 0x14 frame
                raw14 = bytearray.fromhex(reply_0x14)
                raw14[-1] = protocol.crc(list(raw14[:-1]))
                reply_0x14 = raw14.hex().upper()

                async def _lamp_responder() -> None:
                    await asyncio.sleep(0.05)
                    ct = encrypt_mqtt_frame(reply_0x14, TEST_AES_KEY)
                    w_lamp.write(_mqtt_publish_pkt(f"Device/ControlRsp/{mac}", ct))
                    await w_lamp.drain()

                resp_task = asyncio.create_task(_lamp_responder())
                res = await coordinator.async_refresh_state()
                await resp_task
                self.assertEqual(res.replied_addresses, [mac])
                self.assertEqual(protocol.live_master_channels(coordinator.data), (46, 36, 56, 99, 180))

                # Verify Option C entities & unique_ids preserved
                main_light = light.NetleaAquariumLight(coordinator)
                self.assertEqual(main_light._attr_unique_id, "428560_light")
                self.assertEqual(main_light.effect, "原生-溪流")
                self.assertGreater(main_light.extra_state_attributes["power_w"], 0)

                fan_ent = fan.NetleaCoolingFan(coordinator)
                self.assertEqual(fan_ent.percentage, 50)  # PWM 180 = gear 5 = 50%

                rssi_desc = next(d for d in sensor.SENSORS if d.key == "wifi_rssi")
                self.assertEqual(rssi_desc.value_fn(coordinator.data), -46)

                # Verify diagnostics redacts AES key
                entry.runtime_data = coordinator
                diag = await async_get_config_entry_diagnostics(MagicMock(), entry)
                self.assertEqual(diag["entry"]["data"][const.CONF_MQTT_AES_KEY], "**REDACTED**")
            finally:
                w_lamp.close()
        finally:
            await transport.async_stop()

    async def test_option_c_control_mode_and_atmosphere_follow_exit(self) -> None:
        sent_frames: list[str] = []

        class _FakeTransport(LocalMqttServerTransport):
            def __init__(self) -> None:
                super().__init__(allowed_addresses=["AA1122334455"], aes_key_hex=TEST_AES_KEY_HEX)

            async def async_send_frame(self, target_address: str, frame: str, **kwargs):
                sent_frames.append(frame)
                # Return a valid 0x14 echoing atmosphere in followType=1 initially
                payload = [1, 13, 0, 1, 0, 3, 25, 20, 33, 57, 145, 0, 0] + [0] * 9 + [1, 0, 0, 1, 0, 1, 70, 1, 0]
                rep = protocol.build_app_frame(0x01, 0x14, payload)
                return protocol.ControlResult(
                    frame=frame,
                    replies=[{"msgBody": {"realAddress": "AA1122334455", "message": rep}}],
                    expected_addresses=["AA1122334455"],
                    replied_addresses=["AA1122334455"],
                    missing_addresses=[],
                    legacy_state=protocol.decode_lamps_state_reply(rep),
                )

        entry = ConfigEntry(
            entry_id="e_optc",
            data={
                const.CONF_TRANSPORT_MODE: const.TRANSPORT_LOCAL_MQTT,
                const.CONF_EQUIPMENT_USER_ID: "428560",
                const.CONF_CONTROL_REAL_ADDRESS: "AA1122334455",
                const.CONF_MODEL: "NL-N7-90-T32",
            },
        )
        coord = NetleaCoordinator(MagicMock(), _FakeTransport(), entry)
        main_light = light.NetleaAquariumLight(coord)
        mode_sel = select.NetleaControlModeSelect(coord)
        atmos_light = light.NetleaAtmosphereLight(coord)

        # Default control_mode = schedule_auto -> color change uses 0x1A temp frame (60 min)
        self.assertEqual(mode_sel.current_option, const.CONTROL_MODE_SCHEDULE_AUTO)
        await main_light.async_turn_on(rgbw_color=(255, 100, 50, 80), brightness=128)
        self.assertEqual(bytes.fromhex(sent_frames[-1])[3], 0x1A)

        # Switch control_mode = manual_hold -> color change uses 0x10 persistent manual frame
        await mode_sel.async_select_option(const.CONTROL_MODE_MANUAL_HOLD)
        await main_light.async_turn_on(rgbw_color=(255, 100, 50, 80), brightness=128)
        self.assertEqual(bytes.fromhex(sent_frames[-1])[3], 0x10)

        # Atmosphere light while in followType=1 must send 0x22(0) before 0x1E
        sent_frames.clear()
        await atmos_light.async_turn_on(brightness=200)
        self.assertEqual([bytes.fromhex(f)[3] for f in sent_frames], [0x22, 0x1E])
        self.assertEqual(bytes.fromhex(sent_frames[0])[6], 0)  # followType=0

    async def test_v1_to_v2_migration_and_local_config_flow(self) -> None:
        v1_entry = ConfigEntry(
            entry_id="v1_legacy",
            version=1,
            data={
                const.CONF_EQUIPMENT_USER_ID: "428560",
                const.CONF_CONTROL_REAL_ADDRESS: "08927278CCE6,ACEBE6B8786E,08927277FB88",
                const.CONF_WS_URL: const.LEGACY_WS_URL,
            },
        )
        hass = MagicMock()
        hass.config_entries.async_update_entry = MagicMock(
            side_effect=lambda e, *, data, version=None: (setattr(e, "data", data), setattr(e, "version", version or e.version))
        )
        self.assertTrue(await netlea_init.async_migrate_entry(hass, v1_entry))
        self.assertEqual(v1_entry.version, 2)
        self.assertEqual(v1_entry.data[const.CONF_TRANSPORT_MODE], const.TRANSPORT_CLOUD_WS)
        self.assertEqual(v1_entry.data[const.CONF_WS_URL], const.WS_URL)

        # Local MQTT ConfigFlow creates entry without SMS
        flow = NetleaConfigFlow()
        res = await flow.async_step_local_mqtt(
            {
                const.CONF_DEVICE_NAME: "草缸三灯",
                const.CONF_MODEL: "NL-N7-90-T32",
                const.CONF_CONTROL_REAL_ADDRESS: "08:92:72:78:CC:E6, AC:EB:E6:B8:78:6E",
                const.CONF_MQTT_PORT: 11883,
                const.CONF_MQTT_AES_KEY: TEST_AES_KEY_HEX,
            }
        )
        self.assertEqual(res["type"], "create_entry")
        self.assertEqual(res["data"][const.CONF_CONTROL_REAL_ADDRESS], "08927278CCE6,ACEBE6B8786E")
        self.assertEqual(res["data"][const.CONF_TRANSPORT_MODE], const.TRANSPORT_LOCAL_MQTT)

    async def test_sibling_empty_schedule_auto_heal(self) -> None:
        sent: list[tuple[str, int]] = []

        class _FakeGroupTransport(LocalMqttServerTransport):
            def __init__(self) -> None:
                super().__init__(
                    allowed_addresses=["08927278CCE6", "ACEBE6B8786E", "08927277FB8A"],
                    aes_key_hex=TEST_AES_KEY_HEX,
                )

            def connected_addresses(self) -> list[str]:
                return ["08927277FB8A", "08927278CCE6", "ACEBE6B8786E"]

            async def async_send_frame(self, target_address: str, frame: str, **kwargs):
                cmd = bytes.fromhex(frame)[3]
                sent.append((target_address, cmd))
                payload = [1, 13, 0, 1, 0, 2, 46, 36, 56, 99, 180, 0, 0] + [0] * 18
                rep = protocol.build_app_frame(0x01, 0x14, payload)
                return protocol.ControlResult(
                    frame=frame,
                    replies=[{"msgBody": {"realAddress": target_address, "message": rep}}],
                    expected_addresses=[target_address],
                    replied_addresses=[target_address],
                    missing_addresses=[],
                    legacy_state=protocol.decode_lamps_state_reply(rep),
                )

        entry = ConfigEntry(
            entry_id="e_heal",
            data={
                const.CONF_TRANSPORT_MODE: const.TRANSPORT_LOCAL_MQTT,
                const.CONF_EQUIPMENT_USER_ID: "428560",
                const.CONF_CONTROL_REAL_ADDRESS: "08927278CCE6,ACEBE6B8786E,08927277FB8A",
                const.CONF_MODEL: "NL-N7-90-T32",
            },
        )
        coord = NetleaCoordinator(MagicMock(), _FakeGroupTransport(), entry)
        valid_12 = protocol.build_schedule_frame_0x12(
            [{"startH": 13, "startM": 0, "endH": 23, "endM": 0, "R": 46, "W": 36, "G": 56, "B": 99, "F": 180, "mode": 1, "grade": 16}],
            enable_schedule=True,
        )
        empty_12 = protocol.build_schedule_frame_0x12([], enable_schedule=False)

        # Case 1: Empty Lamp-2 connects BEFORE Lamp-1
        coord._on_transport_push("ACEBE6B8786E", "control_rsp", empty_12)
        self.assertIn("ACEBE6B8786E", coord._empty_schedule_macs)
        self.assertEqual(sent, [])

        # Lamp-1 connects with valid schedule -> triggers auto-heal on Lamp-2
        coord._on_transport_push("08927278CCE6", "control_rsp", valid_12)
        # Case 2: Empty Lamp-3 connects AFTER Lamp-1 -> does NOT wipe schedule, triggers auto-heal on Lamp-3
        coord._on_transport_push("08927277FB8A", "control_rsp", empty_12)
        self.assertTrue(protocol.has_main_travel(coord.data))

        if coord._heal_tasks:
            await asyncio.gather(*list(coord._heal_tasks))

        self.assertIn(("ACEBE6B8786E", 0x12), sent)
        self.assertIn(("ACEBE6B8786E", 0x10), sent)
        self.assertIn(("08927277FB8A", 0x12), sent)
        self.assertIn(("08927277FB8A", 0x10), sent)
        self.assertEqual(coord._empty_schedule_macs, set())
