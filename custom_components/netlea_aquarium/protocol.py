"""Pure Netlea protocol framing, reply decoding, and schedule inference.

Zero I/O and zero Home Assistant imports.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
import json
import re
from typing import Any

from . import const

_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
_MASTER_KEYS = ("stateType", "startH", "startM", "lampState", "lampMode", "pattern", "R", "G", "W", "B", "F", "reserve1", "reserve2")
_SMALL_KEYS = ("stateType", "startH", "startM", "lampState", "lampMode", "pattern", "moodLamp", "followType", "reserve1")


@dataclass(frozen=True, slots=True)
class ControlResult:
    """Immutable result of one WebSocket control command."""

    frame: str
    replies: list[Any]
    expected_addresses: list[str]
    replied_addresses: list[str]
    missing_addresses: list[str]
    legacy_state: dict[str, Any] | None


def extract(data: Any, *paths: str) -> Any:
    """Extract the first non-empty value from dotted dict paths."""
    for path in paths:
        cur = data
        for key in path.split("."):
            cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, ""):
            return cur
    return None


def as_int(value: Any, default: int | None = None) -> int | None:
    """Parse a cloud value as int when possible."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def as_bool(value: Any) -> bool | None:
    """Parse a cloud value as bool when possible."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return None


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Parse and clamp an integer within [minimum, maximum]."""
    parsed = as_int(value, default)
    return max(minimum, min(default if parsed is None else parsed, maximum))


def split_addresses(value: Any) -> list[str]:
    """Split comma-separated uppercase device addresses."""
    return [part.strip().upper() for part in str(value or "").split(",") if part.strip()]


def parse_status_log(row: dict[str, Any] | None) -> dict[str, Any]:
    """Parse the statusLog JSON field from a device row."""
    if not isinstance(row, dict):
        return {}
    value = row.get("statusLog")
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_device_row(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize one Netlea cloud device row."""
    status = parse_status_log(row)
    real_address = str(
        row.get("controlRealAddress") or row.get("realAddress")
        or status.get("realAddress") or status.get("onLineRealAddress") or ""
    ).strip().upper()
    addrs = split_addresses(real_address)
    return {
        **row,
        "name": str(row.get("name") or status.get("showName") or "Netlea"),
        "realAddress": real_address,
        "controlRealAddress": str(row.get("controlRealAddress") or real_address).strip().upper(),
        "firstConnectRealAddress": str(
            status.get("firstConnectRealAddress") or row.get("firstConnectRealAddress") or (addrs[0] if addrs else "")
        ).strip().upper(),
        "statusLog": status,
    }


def crc(values: list[int] | bytes) -> int:
    """Compute Netlea modulo-256 frame checksum."""
    return sum(values) % 256


def hex_bytes(value: str | None) -> list[int]:
    """Decode one contiguous hex string into byte integers."""
    text = str(value or "").strip()
    if not text or len(text) % 2 != 0 or not _HEX_RE.fullmatch(text):
        return []
    return [int(text[i : i + 2], 16) for i in range(0, len(text), 2)]


def build_app_frame(device_type: int, command: int, payload: list[int] | None = None) -> str:
    """Build one 0x5A Netlea control frame as uppercase hex."""
    frame = [0x5A, 0, device_type & 0xFF, command & 0xFF, 0, 0, *(payload or ())]
    frame[1] = len(frame) + 1
    frame.append(crc(frame))
    return "".join(f"{b & 0xFF:02X}" for b in frame)


def build_temp_light_frame(
    on: bool,
    red: int = const.DEFAULT_TEMP_RED,
    white: int = const.DEFAULT_TEMP_WHITE,
    green: int = const.DEFAULT_TEMP_GREEN,
    blue: int = const.DEFAULT_TEMP_BLUE,
    fan: int = const.DEFAULT_TEMP_FAN,
    *,
    on_seconds: int = const.DEFAULT_TEMP_ON_SECONDS,
    off_seconds: int = const.DEFAULT_TEMP_OFF_SECONDS,
    mode_id: int = const.DEFAULT_TEMP_MODE_ID,
    grade: int = const.DEFAULT_TEMP_GRADE,
) -> str:
    """Build the 0x1A temporary viewing light on/off frame."""
    if on:
        red = clamp_int(red, const.DEFAULT_TEMP_RED, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL)
        white = clamp_int(white, const.DEFAULT_TEMP_WHITE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL)
        green = clamp_int(green, const.DEFAULT_TEMP_GREEN, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL)
        blue = clamp_int(blue, const.DEFAULT_TEMP_BLUE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL)
        fan = clamp_int(fan, const.DEFAULT_TEMP_FAN, const.MIN_TEMP_FAN, const.MAX_TEMP_FAN)
    else:
        red = white = green = blue = fan = mode_id = grade = 0
    return build_app_frame(
        0x01,
        0x1A,
        [
            on_seconds & 0xFF, (on_seconds >> 8) & 0xFF, off_seconds & 0xFF, (off_seconds >> 8) & 0xFF,
            red & 0xFF, white & 0xFF, green & 0xFF, blue & 0xFF, fan & 0xFF,
            mode_id & 0xFF, grade & 0xFF, (grade >> 8) & 0xFF,
        ],
    )


def find_active_main_trip(status: dict[str, Any], now: datetime | None = None) -> dict[str, Any] | None:
    """Find the currently active main-light schedule trip."""
    current = now or datetime.now().astimezone()
    trips = ((status.get("masterhand") or {}).get("travelList")) or []
    if not isinstance(trips, list):
        return None
    now_min = current.hour * 60 + current.minute
    for trip in trips:
        if not isinstance(trip, dict) or as_int(trip.get("enable"), 0) != 1:
            continue
        days = trip.get("dayList")
        if isinstance(days, list) and len(days) >= 7 and as_bool(days[current.weekday()]) is False:
            continue
        start = (as_int(trip.get("startH"), 0) or 0) * 60 + (as_int(trip.get("startM"), 0) or 0)
        end = (as_int(trip.get("endH"), 0) or 0) * 60 + (as_int(trip.get("endM"), 0) or 0)
        if start == end or (start <= now_min < end if start < end else (now_min >= start or now_min < end)):
            return trip
    return None


def current_trip_name(row: dict[str, Any], now: datetime | None = None) -> str | None:
    """Return the active trip name, if any."""
    trip = find_active_main_trip(parse_status_log(row), now)
    return str(trip.get("name") or "") if trip else None


def _main_flag(status: dict[str, Any], key: str) -> tuple[bool, bool | None]:
    value = status.get(key)
    if isinstance(value, dict) and "main" in value:
        return True, as_bool(value.get("main"))
    return False, as_bool(value) if not isinstance(value, dict) else None


def has_main_travel(row: dict[str, Any]) -> bool:
    """Return whether the main light has configured schedule trips."""
    status = parse_status_log(row)
    has_key, parsed = _main_flag(status, "haveTravel")
    if has_key:
        return bool(parsed)
    trips = ((status.get("masterhand") or {}).get("travelList")) or []
    return bool(trips) if isinstance(trips, list) else False


def is_main_travel_running(row: dict[str, Any]) -> bool:
    """Return whether the main light is currently running its schedule."""
    status = parse_status_log(row)
    has_key, parsed = _main_flag(status, "travelRunning")
    if has_key:
        return bool(parsed)
    return bool(as_bool((status.get("masterhand") or {}).get("traveRunning")))


def is_main_light_on(row: dict[str, Any]) -> bool | None:
    """Return current main-light on/off state."""
    onoff = as_int(parse_status_log(row).get("onoff"))
    return bool(onoff) if onoff in (0, 1) else None


def mode_summary(row: dict[str, Any], now: datetime | None = None) -> str:
    """Return a concise Chinese summary of the current light mode."""
    if is_main_travel_running(row):
        name = current_trip_name(row, now)
        return f"日程中（{name}）" if name else "日程中"
    is_on = is_main_light_on(row)
    if is_on is True:
        return "临时开灯"
    if is_on is False:
        return "临时关灯" if has_main_travel(row) else "关灯"
    return "未知"


def online_lamp_count(row: dict[str, Any]) -> int:
    """Return count of online lamps reported for the device group."""
    status = parse_status_log(row)
    return len(split_addresses(status.get("onLineRealAddress") or row.get("controlRealAddress") or row.get("realAddress")))


def infer_resume_lamp_state(status_log: dict[str, Any] | None, now: datetime | None = None) -> int:
    """Infer whether the lamp should be on (1) or off (0) when resuming schedule."""
    status = (
        parse_status_log(status_log)
        if isinstance(status_log, dict) and "statusLog" in status_log
        else (status_log if isinstance(status_log, dict) else {})
    )
    if find_active_main_trip(status, now):
        return 1
    if _main_flag(status, "haveTravel")[1]:
        return 0
    running = _main_flag(status, "travelRunning")[1]
    if running is not None:
        return 1 if running else 0
    return 1 if as_int(status.get("onoff")) == 1 else 0


def build_resume_schedule_frame(status_log: dict[str, Any] | None, now: datetime | None = None) -> str:
    """Build the 0x10 resume-schedule control frame."""
    return build_app_frame(0x01, 0x10, [infer_resume_lamp_state(status_log, now) & 0xFF, *([0] * 15)])


def _reply_bodies(replies: list[Any]) -> Iterator[dict[str, Any]]:
    for item in replies:
        body = item.get("msgBody") if isinstance(item, dict) else None
        if isinstance(body, dict):
            yield body


def reply_real_addresses(replies: list[Any]) -> list[str]:
    """Collect unique uppercase realAddress values from WebSocket replies."""
    seen: dict[str, None] = {}
    for body in _reply_bodies(replies):
        if addr := str(body.get("realAddress") or "").strip().upper():
            seen.setdefault(addr, None)
    return list(seen)


def decode_lamps_state_reply(message_hex: str | None) -> dict[str, Any] | None:
    """Decode one 0x14 lamp state reply frame."""
    frame = hex_bytes(message_hex)
    if len(frame) < 38 or frame[0] != 0x5A or frame[2] != 0x01 or frame[3] != 0x14:
        return None
    payload = frame[6:-1]
    if len(payload) < 31:
        return None
    return {
        "master": {k: int(payload[i]) for i, k in enumerate(_MASTER_KEYS)},
        "spot": {k: int(payload[13 + i]) for i, k in enumerate(_SMALL_KEYS)},
        "atmosphere": {k: int(payload[22 + i]) for i, k in enumerate(_SMALL_KEYS)},
    }


def lamps_state_from_replies(replies: list[Any]) -> dict[str, Any] | None:
    """Return the first decoded 0x14 lamp state from WebSocket replies."""
    for body in _reply_bodies(replies):
        if parsed := decode_lamps_state_reply(body.get("message")):
            return parsed
    return None
