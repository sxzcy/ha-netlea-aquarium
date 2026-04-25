"""Netlea cloud client and protocol helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import asyncio
import json
import re
import time
from typing import Any

import aiohttp

from .const import (
    API_BASE,
    CONF_API_BASE,
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_WS_URL,
    WS_URL,
)


class NetleaError(Exception):
    """Base Netlea error."""


class NetleaAuthError(NetleaError):
    """Authentication or session error."""


@dataclass(slots=True)
class ControlResult:
    """Result of one websocket control command."""

    frame: str
    replies: list[Any]
    replied_addresses: list[str]
    legacy_state: dict[str, Any] | None


def api_headers(token: str | None = None) -> dict[str, str]:
    """Build request headers."""
    headers = {
        "Accept": "application/json",
        "Accept-Language": "zh-CN",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 NetleaLocalCLI",
    }
    if token:
        headers["Authorization"] = token
    return headers


def extract(data: Any, *paths: str) -> Any:
    """Extract one value from nested dict paths."""
    for path in paths:
        current = data
        for key in path.split("."):
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                current = None
                break
        if current not in (None, ""):
            return current
    return None


def parse_status_log(row: dict[str, Any]) -> dict[str, Any]:
    """Parse statusLog JSON."""
    value = row.get("statusLog")
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def split_addresses(value: Any) -> list[str]:
    """Split comma-separated addresses."""
    return [part.strip().upper() for part in str(value or "").split(",") if part.strip()]


def normalize_device_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one cloud device row."""
    status = parse_status_log(row)
    real_address = str(
        row.get("controlRealAddress")
        or row.get("realAddress")
        or status.get("realAddress")
        or status.get("onLineRealAddress")
        or ""
    ).strip().upper()
    return {
        **row,
        "name": str(row.get("name") or status.get("showName") or "Netlea"),
        "realAddress": real_address,
        "controlRealAddress": str(row.get("controlRealAddress") or real_address).strip().upper(),
        "firstConnectRealAddress": str(
            status.get("firstConnectRealAddress")
            or row.get("firstConnectRealAddress")
            or (split_addresses(real_address)[0] if real_address else "")
        ).strip().upper(),
        "statusLog": status,
    }


def hex_byte(value: int) -> str:
    """Format one byte."""
    return f"{int(value) & 0xFF:02X}"


def crc(values: list[int]) -> int:
    """Simple Netlea checksum."""
    return sum(values) % 256


def app_frame(device_type: int, command: int, payload: list[int] | None = None) -> str:
    """Build one legacy cloud control frame."""
    frame = [0x5A, 0, device_type & 0xFF, command & 0xFF, 0, 0]
    frame.extend(payload or [])
    frame[1] = len(frame) + 1
    frame.append(crc(frame))
    return "".join(hex_byte(value) for value in frame)


def little_endian_bytes(value: int, byte_width: int) -> list[int]:
    """Encode one unsigned integer as little-endian bytes."""
    bounded = max(0, int(value))
    return list(bounded.to_bytes(byte_width, byteorder="little", signed=False))


def hex_bytes(value: str | None) -> list[int]:
    """Parse one contiguous hex string into bytes."""
    text = str(value or "").strip()
    if not text or len(text) % 2 != 0 or not re.fullmatch(r"[0-9A-Fa-f]+", text):
        return []
    return [int(text[index : index + 2], 16) for index in range(0, len(text), 2)]


def legacy_temporary_main_frame(
    *,
    on: bool,
    on_seconds: int = 20,
    off_seconds: int = 20,
    red: int = 25,
    white: int = 20,
    green: int = 33,
    blue: int = 57,
    fan: int = 3,
    mode_id: int = 1,
    grade: int = 9,
) -> str:
    """Build the verified temporary main-light frame."""
    if not on:
        red = white = green = blue = fan = mode_id = grade = 0
    payload = [
        on_seconds & 0xFF,
        (on_seconds >> 8) & 0xFF,
        off_seconds & 0xFF,
        (off_seconds >> 8) & 0xFF,
        red & 0xFF,
        white & 0xFF,
        green & 0xFF,
        blue & 0xFF,
        fan & 0xFF,
        mode_id & 0xFF,
        grade & 0xFF,
        (grade >> 8) & 0xFF,
    ]
    return app_frame(0x01, 0x1A, payload)


def active_on_day(day_list: Any, now: datetime) -> bool:
    """Check whether one trip is enabled for today."""
    if not isinstance(day_list, list) or len(day_list) < 7:
        return True
    return str(day_list[now.weekday()]) == "1"


def active_in_time_window(trip: dict[str, Any], now: datetime) -> bool:
    """Check whether the current time falls into one trip."""
    start = int(trip.get("startH") or 0) * 60 + int(trip.get("startM") or 0)
    end = int(trip.get("endH") or 0) * 60 + int(trip.get("endM") or 0)
    current = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def find_active_main_trip(status: dict[str, Any]) -> dict[str, Any] | None:
    """Find the currently active main-light trip."""
    now = datetime.now()
    trips = ((status.get("masterhand") or {}).get("travelList")) or []
    for trip in trips:
        if int(trip.get("enable") or 0) != 1:
            continue
        if not active_on_day(trip.get("dayList"), now):
            continue
        if active_in_time_window(trip, now):
            return trip
    return None


def current_trip_name(row: dict[str, Any]) -> str | None:
    """Return the active trip name."""
    trip = find_active_main_trip(parse_status_log(row))
    return str(trip.get("name") or "") if trip else None


def has_main_travel(row: dict[str, Any]) -> bool:
    """Return whether the main light has configured trips."""
    status = parse_status_log(row)
    value = status.get("haveTravel")
    if isinstance(value, dict) and "main" in value:
        return bool(value.get("main"))
    trips = ((status.get("masterhand") or {}).get("travelList")) or []
    return bool(trips)


def is_main_travel_running(row: dict[str, Any]) -> bool:
    """Return whether the main light is currently running a trip."""
    status = parse_status_log(row)
    value = status.get("travelRunning")
    if isinstance(value, dict) and "main" in value:
        return bool(value.get("main"))
    return bool((status.get("masterhand") or {}).get("traveRunning"))


def is_main_light_on(row: dict[str, Any]) -> bool | None:
    """Return current on/off state."""
    status = parse_status_log(row)
    onoff = status.get("onoff")
    if onoff in (0, 1):
        return bool(onoff)
    return None


def mode_summary(row: dict[str, Any]) -> str:
    """Build a concise Chinese mode summary."""
    if is_main_travel_running(row):
        name = current_trip_name(row)
        return f"行程中{name and f'（{name}）' or ''}"
    is_on = is_main_light_on(row)
    if is_on is True:
        return "临时开灯"
    if is_on is False and has_main_travel(row):
        return "临时关灯"
    if is_on is False:
        return "关灯"
    return "未知"


def infer_resume_lamp_state(target: dict[str, Any] | None) -> int:
    """Infer whether the current schedule should be on when resuming."""
    status = parse_status_log(target or {})
    travel_running = (status.get("travelRunning") or {}).get("main")
    if isinstance(travel_running, bool):
        return 1 if travel_running else 0
    if find_active_main_trip(status):
        return 1
    have_travel = (status.get("haveTravel") or {}).get("main")
    if have_travel:
        return 1
    onoff = status.get("onoff")
    return 1 if onoff == 1 else 0


def legacy_resume_schedule_frame(target: dict[str, Any] | None) -> str:
    """Build the verified resume-schedule frame."""
    lamp_state = infer_resume_lamp_state(target)
    payload = [
        lamp_state & 0xFF,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        *little_endian_bytes(0, 2),
        0,
        *little_endian_bytes(0, 2),
        0,
        0,
    ]
    return app_frame(0x01, 0x10, payload)


def reply_real_addresses(replies: list[Any]) -> list[str]:
    """Collect unique realAddress values from websocket replies."""
    values: list[str] = []
    seen: set[str] = set()
    for item in replies:
        if not isinstance(item, dict):
            continue
        body = item.get("msgBody")
        if not isinstance(body, dict):
            continue
        real_address = str(body.get("realAddress") or "").strip().upper()
        if not real_address or real_address in seen:
            continue
        seen.add(real_address)
        values.append(real_address)
    return values


def decode_legacy_lamps_state_reply(message_hex: str | None) -> dict[str, Any] | None:
    """Decode a legacy lamp state reply produced by commands 0x10/0x1A."""
    frame = hex_bytes(message_hex)
    if len(frame) < 38 or frame[0] != 0x5A or frame[2] != 0x01 or frame[3] != 0x14:
        return None
    payload = frame[6:-1]
    if len(payload) < 31:
        return None
    master_keys = [
        "stateType",
        "startH",
        "startM",
        "lampState",
        "lampMode",
        "pattern",
        "R",
        "G",
        "W",
        "B",
        "F",
        "reserve1",
        "reserve2",
    ]
    small_keys = [
        "stateType",
        "startH",
        "startM",
        "lampState",
        "lampMode",
        "pattern",
        "moodLamp",
        "followType",
        "reserve1",
    ]
    return {
        "master": {key: int(payload[index]) for index, key in enumerate(master_keys)},
        "spot": {key: int(payload[13 + index]) for index, key in enumerate(small_keys)},
        "atmosphere": {key: int(payload[22 + index]) for index, key in enumerate(small_keys)},
    }


def legacy_lamps_state_from_replies(replies: list[Any]) -> dict[str, Any] | None:
    """Extract the first decoded legacy lamp state from websocket replies."""
    for reply in replies:
        if not isinstance(reply, dict):
            continue
        body = reply.get("msgBody")
        if not isinstance(body, dict):
            continue
        parsed = decode_legacy_lamps_state_reply(body.get("message"))
        if parsed:
            return parsed
    return None


class NetleaClient:
    """Async Netlea cloud client."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_base: str = API_BASE,
        ws_url: str = WS_URL,
        token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.api_base = api_base.rstrip("/")
        self.ws_url = ws_url
        self.token = token
        self.user_id = user_id

    @classmethod
    def from_config_entry_data(cls, session: aiohttp.ClientSession, data: dict[str, Any]) -> "NetleaClient":
        """Create a client from config entry data."""
        return cls(
            session,
            api_base=str(data.get(CONF_API_BASE) or API_BASE),
            ws_url=str(data.get(CONF_WS_URL) or WS_URL),
            token=str(data.get(CONF_TOKEN) or ""),
            user_id=str(data.get(CONF_USER_ID) or ""),
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        """Make one Netlea HTTP request."""
        async with self._session.request(
            method,
            url,
            params=params,
            json=payload,
            headers=api_headers(token),
        ) as response:
            text = await response.text()

        if response.status in (401, 403):
            raise NetleaAuthError(text)
        if response.status >= 400:
            raise NetleaError(f"HTTP {response.status}: {text[:300]}")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise NetleaError(f"返回不是合法 JSON: {text[:300]}") from err

        if isinstance(data, dict):
            code = data.get("code")
            msg = str(data.get("msg") or "").strip()
            if code in (401, 403, 409):
                raise NetleaAuthError(msg or text[:300])
            if code not in (None, 200):
                raise NetleaError(msg or text[:300])
        return data

    async def async_send_code(self, phone: str) -> str:
        """Send an SMS login code."""
        data = await self._request_json(
            "GET",
            f"{self.api_base}/V3/Api/captchaMsg",
            params={"captchaMsgFlag": "3", "phonenumber": phone},
        )
        uuid = extract(data, "uuid", "data.uuid")
        if not uuid:
            raise NetleaError(f"发码成功但没拿到 uuid: {data!r}")
        return str(uuid)

    async def async_login(self, phone: str, code: str, uuid: str) -> dict[str, Any]:
        """Login with SMS code and fetch devices."""
        data = await self._request_json(
            "POST",
            f"{self.api_base}/V3/Api/appuser/loginBycode",
            payload={
                "username": phone,
                "code": code,
                "uuid": uuid,
            },
        )
        token = extract(data, "token", "data.token")
        user_id = extract(data, "userId", "data.userId")
        if not token or user_id in (None, ""):
            raise NetleaAuthError(f"登录成功但没拿到 token/userId: {data!r}")

        self.token = str(token)
        self.user_id = str(user_id)
        devices = await self.async_fetch_devices()
        return {
            "token": self.token,
            "user_id": self.user_id,
            "devices": devices,
        }

    async def async_fetch_devices(self) -> list[dict[str, Any]]:
        """Fetch devices from Netlea cloud."""
        if not self.token or not self.user_id:
            raise NetleaAuthError("缺少 token/user_id")
        data = await self._request_json(
            "POST",
            f"{self.api_base}/V3/Api/batchSyncEquipmentUser",
            token=self.token,
            payload={
                "userId": self.user_id,
                "client": 0,
                "equipmentUserEntityList": [],
            },
        )
        rows = data.get("rows") if isinstance(data, dict) else None
        return [normalize_device_row(row) for row in rows] if isinstance(rows, list) else []

    async def async_send_frame(self, target_address: str, frame: str, timeout: float = 8.0) -> ControlResult:
        """Send one websocket control frame."""
        if not self.token:
            raise NetleaAuthError("缺少 token")
        replies: list[Any] = []
        expected_addresses = split_addresses(target_address)
        async with self._session.ws_connect(self.ws_url, headers={"token": self.token}, heartbeat=20) as ws:
            payload = {
                "msgType": 1002,
                "msgBody": {
                    "time": int(time.time() * 1000),
                    "all": "1",
                    "topic": f"App/Control/{target_address}",
                    "message": frame,
                },
            }
            await ws.send_str(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                try:
                    incoming = await ws.receive(timeout=max(0.1, deadline - asyncio.get_running_loop().time()))
                except asyncio.TimeoutError:
                    break
                if incoming.type == aiohttp.WSMsgType.TEXT:
                    raw = incoming.data
                elif incoming.type == aiohttp.WSMsgType.BINARY:
                    raw = incoming.data.decode("utf-8", errors="ignore")
                else:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and parsed.get("code") in (401, 403, 409):
                    raise NetleaAuthError(str(parsed.get("msg") or parsed))
                replies.append(parsed)
                if expected_addresses and len(reply_real_addresses(replies)) >= len(expected_addresses):
                    break

        return ControlResult(
            frame=frame,
            replies=replies,
            replied_addresses=reply_real_addresses(replies),
            legacy_state=legacy_lamps_state_from_replies(replies),
        )

    async def async_temp_on(self, target_address: str) -> ControlResult:
        """Temporarily turn the main light on."""
        return await self.async_send_frame(target_address, legacy_temporary_main_frame(on=True))

    async def async_temp_off(self, target_address: str) -> ControlResult:
        """Temporarily turn the main light off."""
        return await self.async_send_frame(target_address, legacy_temporary_main_frame(on=False))

    async def async_resume_schedule(self, target_address: str, target: dict[str, Any] | None) -> ControlResult:
        """Resume the configured light schedule."""
        return await self.async_send_frame(target_address, legacy_resume_schedule_frame(target))
