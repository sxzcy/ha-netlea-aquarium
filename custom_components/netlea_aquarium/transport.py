"""Transport backends for Netlea Aquarium: Local MQTT server, Local BLE, and Cloud WS."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Callable
from datetime import datetime
import logging
import struct
import time
from typing import Any

from . import const, protocol as proto
from .client import NetleaClient, NetleaConnectionError, NetleaControlAckError, NetleaError

_LOGGER = logging.getLogger(__name__)

PushCallback = Callable[[str, str, str], None]


def parse_aes_key(key_hex: str | None) -> bytes:
    """Validate and decode a 32-character hex AES-128 key."""
    cleaned = str(key_hex or "").strip()
    if len(cleaned) != 32:
        raise NetleaError("Local MQTT AES key must be a 32-character hex string (16 bytes)")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as err:
        raise NetleaError("Local MQTT AES key contains invalid hex characters") from err


def encrypt_mqtt_frame(frame_hex: str, key: bytes) -> bytes:
    """Encrypt a 0x5A hex frame with zero-padded AES-128-ECB (matches ESP32-C3 firmware 0000006F)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    raw = bytes.fromhex(frame_hex)
    rem = len(raw) % 16
    if rem:
        raw = raw + b"\x00" * (16 - rem)
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(raw) + enc.finalize()


def decrypt_mqtt_frame(ciphertext: bytes, key: bytes) -> str | None:
    """Decrypt a zero-padded AES-128-ECB MQTT payload into a validated uppercase 0x5A hex frame."""
    if not ciphertext or (len(ciphertext) % 16) != 0:
        return None
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    pt = dec.update(ciphertext) + dec.finalize()
    if len(pt) < 7 or pt[0] != 0x5A:
        return None
    frame_len = pt[1]
    if frame_len < 7 or frame_len > len(pt):
        return None
    frame = pt[:frame_len]
    if not proto.verify_frame_bytes(frame):
        return None
    return frame.hex().upper()


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        d = n % 128
        n //= 128
        if n > 0:
            d |= 0x80
        out.append(d)
        if n == 0:
            break
    return bytes(out)


def _read_mqtt_str(buf: bytes, off: int) -> tuple[str, int]:
    if off + 2 > len(buf):
        raise ValueError("Truncated MQTT string length")
    ln = struct.unpack("!H", buf[off : off + 2])[0]
    off += 2
    if off + ln > len(buf):
        raise ValueError("Truncated MQTT string body")
    return buf[off : off + ln].decode("utf-8", errors="replace"), off + ln


def _build_mqtt_publish(topic: str, payload: bytes, *, qos: int = 1, pid: int = 1) -> bytes:
    tb = topic.encode("utf-8")
    if qos == 1:
        body = struct.pack("!H", len(tb)) + tb + struct.pack("!H", pid & 0xFFFF) + payload
        hdr = 0x32
    else:
        body = struct.pack("!H", len(tb)) + tb + payload
        hdr = 0x30
    return bytes([hdr]) + _encode_varint(len(body)) + body


class NetleaTransport(ABC):
    """Abstract transport interface for Netlea lamp groups."""

    transport_type: str = const.TRANSPORT_CLOUD_WS

    def __init__(self) -> None:
        self._push_cb: PushCallback | None = None

    def set_push_callback(self, callback: PushCallback | None) -> None:
        """Register callback(mac, kind, frame_hex) for unsolicited local push updates."""
        self._push_cb = callback

    async def async_start(self) -> None:
        """Start background listeners if any."""

    async def async_stop(self) -> None:
        """Stop background listeners and release resources."""

    def connected_addresses(self) -> list[str]:
        """Return currently connected/online lamp MAC addresses."""
        return []

    @abstractmethod
    async def async_send_frame(
        self,
        target_address: str,
        frame: str,
        *,
        timeout: float = const.DEFAULT_CONTROL_REPLY_TIMEOUT,
        expect_cmd: int | None = None,
    ) -> proto.ControlResult:
        """Send one 0x5A hex frame to target lamp address(es) and collect replies."""


class LocalMqttServerTransport(NetleaTransport):
    """Embedded lightweight asyncio MQTT 3.1.1 server for direct LAN local_push."""

    transport_type = const.TRANSPORT_LOCAL_MQTT

    def __init__(
        self,
        *,
        allowed_addresses: list[str],
        aes_key_hex: str,
        bind_host: str = const.DEFAULT_MQTT_BIND_HOST,
        port: int = const.DEFAULT_MQTT_PORT,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__()
        self._allowed: set[str] = {proto.normalize_mac(a) for a in allowed_addresses if proto.normalize_mac(a)}
        self._key: bytes = parse_aes_key(aes_key_hex)
        self._bind_host = bind_host
        self._port = int(port)
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now().astimezone())
        self._server: asyncio.AbstractServer | None = None
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._last_seen: dict[str, float] = {}
        self._pending: dict[str, list[tuple[int | None, asyncio.Future[str]]]] = {}
        self._pid = 1
        self._send_lock = asyncio.Lock()
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    @property
    def bound_port(self) -> int:
        if self._server and self._server.sockets:
            return int(self._server.sockets[0].getsockname()[1])
        return self._port

    def connected_addresses(self) -> list[str]:
        return sorted(mac for mac, writer in self._clients.items() if not writer.is_closing())

    async def async_start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._bind_host,
            port=self._port,
            reuse_address=True,
        )
        _LOGGER.info(
            "Netlea embedded Local MQTT server listening on %s:%s (whitelisted MACs: %d)",
            self._bind_host,
            self.bound_port,
            len(self._allowed),
        )

    async def async_stop(self) -> None:
        for task in list(self._bg_tasks):
            task.cancel()
        self._bg_tasks.clear()
        for writer in list(self._clients.values()):
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._clients.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def _next_pid(self) -> int:
        self._pid = (self._pid % 65534) + 1
        return self._pid

    def _spawn_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client_id = ""
        subscribed_ctrl = False
        try:
            while True:
                hdr = await asyncio.wait_for(reader.readexactly(1), timeout=300.0)
                ptype = (hdr[0] >> 4) & 0x0F
                flags = hdr[0] & 0x0F
                rem_len = 0
                mult = 1
                for _ in range(4):
                    b = (await reader.readexactly(1))[0]
                    rem_len += (b & 0x7F) * mult
                    if (b & 0x80) == 0:
                        break
                    mult *= 128
                if rem_len > 65536:
                    break
                body = await reader.readexactly(rem_len) if rem_len else b""

                if ptype == 1:  # CONNECT
                    _, off = _read_mqtt_str(body, 0)
                    off += 4  # proto_level(1) + flags(1) + keepalive(2)
                    raw_cid, _ = _read_mqtt_str(body, off)
                    cid = proto.normalize_mac(raw_cid)
                    if self._allowed and cid not in self._allowed:
                        _LOGGER.warning("Rejected unauthorized Local MQTT client_id=%s", raw_cid)
                        writer.write(b"\x20\x02\x00\x05")  # CONNACK Not Authorized
                        await writer.drain()
                        break
                    client_id = cid or raw_cid.upper()
                    old = self._clients.get(client_id)
                    if old is not None and old is not writer and not old.is_closing():
                        old.close()
                    self._clients[client_id] = writer
                    self._last_seen[client_id] = time.monotonic()
                    writer.write(b"\x20\x02\x00\x00")  # CONNACK OK
                    await writer.drain()
                    _LOGGER.debug("Netlea lamp connected to Local MQTT: %s", client_id)

                elif ptype == 8:  # SUBSCRIBE
                    pid = struct.unpack("!H", body[0:2])[0]
                    off = 2
                    rcs = bytearray()
                    topics: list[str] = []
                    while off < len(body):
                        t, off = _read_mqtt_str(body, off)
                        qos = body[off]
                        off += 1
                        topics.append(t)
                        rcs.append(min(qos, 1))
                    suback = bytes([0x90]) + _encode_varint(2 + len(rcs)) + struct.pack("!H", pid) + bytes(rcs)
                    writer.write(suback)
                    await writer.drain()
                    if client_id and not subscribed_ctrl and any(t.startswith("App/Control/") for t in topics):
                        subscribed_ctrl = True
                        self._spawn_task(self._async_on_lamp_ready(client_id))

                elif ptype == 3:  # PUBLISH
                    qos = (flags >> 1) & 0x03
                    topic, off = _read_mqtt_str(body, 0)
                    pid = None
                    if qos > 0:
                        pid = struct.unpack("!H", body[off : off + 2])[0]
                        off += 2
                    payload = body[off:]
                    if qos == 1 and pid is not None:
                        writer.write(b"\x40\x02" + struct.pack("!H", pid))
                        await writer.drain()
                    if client_id:
                        self._last_seen[client_id] = time.monotonic()
                        self._handle_publish(client_id, topic, payload)

                elif ptype == 12:  # PINGREQ
                    if client_id:
                        self._last_seen[client_id] = time.monotonic()
                    writer.write(b"\xD0\x00")
                    await writer.drain()

                elif ptype == 14:  # DISCONNECT
                    break
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, ValueError):
            pass
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Netlea Local MQTT client %s closed: %s", client_id or "?", err)
        finally:
            if client_id and self._clients.get(client_id) is writer:
                self._clients.pop(client_id, None)
                if self._push_cb is not None:
                    self._push_cb(client_id, "disconnect", "")
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    async def _async_on_lamp_ready(self, mac: str) -> None:
        """Sync clock and fetch initial live state + stored 0x12 schedule upon lamp connect."""
        try:
            await asyncio.sleep(0.4)
            await self._publish_raw(mac, proto.build_sync_time_0x04(self._now_fn()))
            await asyncio.sleep(0.35)
            await self._publish_raw(mac, proto.build_query_schedule_0x25(1))
            await asyncio.sleep(0.35)
            await self._publish_raw(mac, proto.build_query_state_frame())
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Initial Local MQTT sync for %s skipped: %s", mac, err)

    def _handle_publish(self, mac: str, topic: str, payload: bytes) -> None:
        frame_hex = decrypt_mqtt_frame(payload, self._key)
        if not frame_hex:
            _LOGGER.debug("Ignored undecryptable Local MQTT payload from %s on %s (%dB)", mac, topic, len(payload))
            return
        cmd = int(frame_hex[6:8], 16)
        if topic.startswith("Device/Heartbeat/"):
            kind = "heartbeat"
        elif topic.startswith("Device/StateReport/"):
            kind = "state_report"
        else:
            kind = "control_rsp"
            # Resolve matching waiters
            waiters = self._pending.get(mac)
            if waiters:
                remaining: list[tuple[int | None, asyncio.Future[str]]] = []
                for expect_cmd, fut in waiters:
                    if not fut.done() and (expect_cmd is None or expect_cmd == cmd):
                        fut.set_result(frame_hex)
                    elif not fut.done():
                        remaining.append((expect_cmd, fut))
                self._pending[mac] = remaining
        if self._push_cb is not None:
            self._push_cb(mac, kind, frame_hex)

    async def _publish_raw(self, mac: str, frame_hex: str) -> bool:
        writer = self._clients.get(mac)
        if writer is None or writer.is_closing():
            return False
        ct = encrypt_mqtt_frame(frame_hex, self._key)
        pkt = _build_mqtt_publish(f"App/Control/{mac}", ct, qos=1, pid=self._next_pid())
        writer.write(pkt)
        await writer.drain()
        return True

    async def async_send_frame(
        self,
        target_address: str,
        frame: str,
        *,
        timeout: float = const.DEFAULT_CONTROL_REPLY_TIMEOUT,
        expect_cmd: int | None = None,
    ) -> proto.ControlResult:
        expected = proto.split_addresses(target_address)
        if not expected:
            raise NetleaError("Missing Netlea control address")
        async with self._send_lock:
            connected = [mac for mac in expected if mac in self._clients and not self._clients[mac].is_closing()]
            if not connected:
                raise NetleaConnectionError(
                    f"No Netlea lamps connected to Local MQTT server on port {self.bound_port} (expected {','.join(expected)})"
                )
            loop = asyncio.get_running_loop()
            futures: dict[str, asyncio.Future[str]] = {}
            for mac in connected:
                fut: asyncio.Future[str] = loop.create_future()
                futures[mac] = fut
                self._pending.setdefault(mac, []).append((expect_cmd, fut))
                await self._publish_raw(mac, frame)

            replies: list[dict[str, Any]] = []
            try:
                done_vals = await asyncio.gather(
                    *(asyncio.wait_for(f, timeout=timeout) for f in futures.values()),
                    return_exceptions=True,
                )
                for mac, res in zip(connected, done_vals, strict=False):
                    if isinstance(res, str):
                        replies.append({"msgBody": {"realAddress": mac, "message": res}})
            finally:
                for mac, fut in futures.items():
                    if mac in self._pending:
                        self._pending[mac] = [(ec, f) for ec, f in self._pending[mac] if f is not fut]

        replied = proto.reply_real_addresses(replies)
        if not replied:
            raise NetleaControlAckError(f"Local MQTT command timed out waiting for reply from {','.join(connected)}")
        return proto.ControlResult(
            frame=frame,
            replies=replies,
            expected_addresses=expected,
            replied_addresses=replied,
            missing_addresses=[a for a in expected if a not in replied],
            legacy_state=proto.lamps_state_from_replies(replies),
        )


class LocalBleTransport(NetleaTransport):
    """Direct local Bluetooth GATT transport using HA bluetooth + bleak."""

    transport_type = const.TRANSPORT_LOCAL_BLE

    def __init__(self, hass: Any, *, allowed_addresses: list[str]) -> None:
        super().__init__()
        self._hass = hass
        self._addresses = [proto.normalize_mac(a) for a in allowed_addresses if proto.normalize_mac(a)]
        self._locks: dict[str, asyncio.Lock] = {mac: asyncio.Lock() for mac in self._addresses}
        self._online: set[str] = set()

    def connected_addresses(self) -> list[str]:
        return sorted(self._online)

    async def _send_one_ble(
        self, mac: str, frame: str, *, timeout: float, expect_cmd: int | None
    ) -> str | None:
        lock = self._locks.setdefault(mac, asyncio.Lock())
        colon_mac = proto.format_mac_colon(mac)
        async with lock:
            ble_device = None
            try:
                from homeassistant.components import bluetooth  # type: ignore[import-not-found]

                ble_device = bluetooth.async_ble_device_from_address(self._hass, colon_mac, connectable=True)
            except Exception:  # noqa: BLE001
                ble_device = None

            from bleak import BleakClient  # type: ignore[import-not-found]

            client = None
            try:
                if ble_device is not None:
                    try:
                        from bleak_retry_connector import establish_connection  # type: ignore[import-not-found]

                        client = await establish_connection(BleakClient, ble_device, colon_mac, max_attempts=2)
                    except Exception:  # noqa: BLE001
                        client = BleakClient(ble_device, timeout=timeout)
                        await client.connect()
                else:
                    client = BleakClient(colon_mac, timeout=timeout)
                    await client.connect()

                loop = asyncio.get_running_loop()
                reply_fut: asyncio.Future[str] = loop.create_future()

                def _on_notify(_sender: Any, data: bytearray) -> None:
                    raw = bytes(data)
                    if not proto.verify_frame_bytes(raw):
                        return
                    hx = raw.hex().upper()
                    cmd = raw[3]
                    if cmd == 0x07:
                        return
                    if not reply_fut.done() and (expect_cmd is None or expect_cmd == cmd):
                        reply_fut.set_result(hx)

                await client.start_notify(const.BLE_NOTIFY_UUID, _on_notify)
                # Send auth 0x06(type=1) before mutating commands (read-only 0x15/0x1B/0x25 work without auth)
                tx_cmd = int(frame[6:8], 16)
                if tx_cmd not in (0x15, 0x1B, 0x25):
                    auth_frame = bytes.fromhex(proto.build_ble_token_0x06(1))
                    await client.write_gatt_char(const.BLE_WRITE_UUID, auth_frame, response=False)
                    await asyncio.sleep(0.15)
                await client.write_gatt_char(const.BLE_WRITE_UUID, bytes.fromhex(frame), response=False)
                reply_hex = await asyncio.wait_for(reply_fut, timeout=timeout)
                self._online.add(mac)
                if self._push_cb is not None:
                    self._push_cb(mac, "control_rsp", reply_hex)
                return reply_hex
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Local BLE command to %s failed: %s", colon_mac, err)
                self._online.discard(mac)
                return None
            finally:
                if client is not None:
                    try:
                        await client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass

    async def async_send_frame(
        self,
        target_address: str,
        frame: str,
        *,
        timeout: float = const.DEFAULT_CONTROL_REPLY_TIMEOUT,
        expect_cmd: int | None = None,
    ) -> proto.ControlResult:
        expected = proto.split_addresses(target_address)
        if not expected:
            raise NetleaError("Missing Netlea control address")
        results = await asyncio.gather(
            *(self._send_one_ble(mac, frame, timeout=timeout, expect_cmd=expect_cmd) for mac in expected)
        )
        replies = [
            {"msgBody": {"realAddress": mac, "message": rep}}
            for mac, rep in zip(expected, results, strict=False)
            if isinstance(rep, str)
        ]
        replied = proto.reply_real_addresses(replies)
        if not replied:
            raise NetleaControlAckError(f"Local BLE command received no reply from {','.join(expected)}")
        return proto.ControlResult(
            frame=frame,
            replies=replies,
            expected_addresses=expected,
            replied_addresses=replied,
            missing_addresses=[a for a in expected if a not in replied],
            legacy_state=proto.lamps_state_from_replies(replies),
        )


class CloudWsTransport(NetleaTransport):
    """Official Netlea cloud REST + WebSocket transport (v1.0.0 compatibility)."""

    transport_type = const.TRANSPORT_CLOUD_WS

    def __init__(self, client: NetleaClient) -> None:
        super().__init__()
        self.client = client

    async def async_send_frame(
        self,
        target_address: str,
        frame: str,
        *,
        timeout: float = const.DEFAULT_CONTROL_REPLY_TIMEOUT,
        expect_cmd: int | None = None,
    ) -> proto.ControlResult:
        return await self.client.async_send_frame(target_address, frame, timeout=timeout)
