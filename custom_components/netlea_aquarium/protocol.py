"""Pure Netlea protocol framing, reply decoding, and schedule codecs.

Zero I/O and zero Home Assistant imports.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
import json
import re
import struct
from typing import Any

from . import const, presets

_HEX_RE = re.compile(r"^[0-9A-Fa-f]+$")
# Verified on real NL-N7-90-T32 firmware 0000006F: 0x14 channel order is R, W, G, B, F
_MASTER_KEYS = (
    "stateType",
    "startH",
    "startM",
    "lampState",
    "lampMode",
    "pattern",
    "R",
    "W",
    "G",
    "B",
    "F",
    "reserve1",
    "reserve2",
)
_SMALL_KEYS = (
    "stateType",
    "startH",
    "startM",
    "lampState",
    "lampMode",
    "pattern",
    "moodLamp",
    "followType",
    "reserve1",
)


@dataclass(frozen=True, slots=True)
class ControlResult:
    """Immutable result of one Netlea control command across any transport."""

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
    """Parse a value as int when possible."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def as_bool(value: Any) -> bool | None:
    """Parse a value as bool when possible."""
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


def normalize_mac(value: Any) -> str:
    """Normalize a BLE/device MAC address to 12 uppercase hex chars without colons."""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).upper()
    return cleaned


def format_mac_colon(mac: str) -> str:
    """Format a 12-char MAC as AA:BB:CC:DD:EE:FF."""
    norm = normalize_mac(mac)
    if len(norm) != 12:
        return norm
    return ":".join(norm[i : i + 2] for i in range(0, 12, 2))


def split_addresses(value: Any) -> list[str]:
    """Split comma-separated uppercase device addresses (accepts colon or bare MACs)."""
    result: list[str] = []
    for part in str(value or "").split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        norm = normalize_mac(cleaned)
        result.append(norm if len(norm) == 12 else cleaned.upper())
    return result


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
    """Normalize one Netlea device row (cloud or local)."""
    status = parse_status_log(row)
    real_address = str(
        row.get("controlRealAddress")
        or row.get("realAddress")
        or status.get("realAddress")
        or status.get("onLineRealAddress")
        or ""
    ).strip().upper()
    addrs = split_addresses(real_address)
    normalized_real = ",".join(addrs) if addrs else real_address
    return {
        **row,
        "name": str(row.get("name") or status.get("showName") or "Netlea"),
        "realAddress": normalized_real,
        "controlRealAddress": ",".join(split_addresses(row.get("controlRealAddress") or normalized_real)),
        "firstConnectRealAddress": str(
            status.get("firstConnectRealAddress")
            or row.get("firstConnectRealAddress")
            or (addrs[0] if addrs else "")
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


def verify_frame_bytes(frame: bytes | list[int]) -> bool:
    """Validate 0x5A frame header, length byte, and modulo-256 checksum."""
    if len(frame) < 7 or frame[0] != 0x5A or frame[1] != len(frame):
        return False
    return crc(frame[:-1]) == frame[-1]


def build_app_frame(device_type: int, command: int, payload: list[int] | None = None) -> str:
    """Build one 0x5A Netlea control frame as uppercase hex."""
    frame = [0x5A, 0, device_type & 0xFF, command & 0xFF, 0, 0, *(payload or ())]
    frame[1] = len(frame) + 1
    frame.append(crc(frame))
    return "".join(f"{b & 0xFF:02X}" for b in frame)


def build_sync_time_0x04(
    now: datetime | None = None,
    *,
    tz_offset_hours: int | None = None,
    group_id: int = 0,
    lamp_index: int = 0,
) -> str:
    """Build 0x04 clock sync frame: [groupId, devIdx, tzByte, YY, MM, DD, hh, mm, ss] (9B, V3 v2.0.2 verified)."""
    current = now or datetime.now().astimezone()
    if tz_offset_hours is None:
        offset = current.utcoffset()
        tz_hours = int(round(offset.total_seconds() / 3600)) if offset is not None else 8
    else:
        tz_hours = int(tz_offset_hours)
    tz_byte = (abs(tz_hours) + 128) if tz_hours < 0 else (tz_hours & 0x7F)
    payload = [
        group_id & 0xFF,
        lamp_index & 0xFF,
        tz_byte & 0xFF,
        (current.year - 2000) & 0xFF,
        current.month & 0xFF,
        current.day & 0xFF,
        current.hour & 0xFF,
        current.minute & 0xFF,
        current.second & 0xFF,
    ]
    return build_app_frame(0x01, 0x04, payload)


def build_ble_token_0x06(auth_type: int = 1, token_int: int = 0) -> str:
    """Build 0x06 BLE token write (auth_type=0) or verify (auth_type=1) frame: [type, u32 BE] (5B)."""
    val = int(token_int) & 0xFFFFFFFF
    return build_app_frame(
        0x01,
        0x06,
        [auth_type & 0xFF, (val >> 24) & 0xFF, (val >> 16) & 0xFF, (val >> 8) & 0xFF, val & 0xFF],
    )


def decode_heartbeat_0x0a(message_hex: str | None) -> dict[str, Any] | None:
    """Decode 0x0A Local MQTT heartbeat frame: 5A 0B 01 0A 00 <rssi_i8> <fw_u32_le> <crc>."""
    frame = hex_bytes(message_hex)
    if len(frame) < 11 or frame[0] != 0x5A or frame[2] != 0x01 or frame[3] != 0x0A:
        return None
    if not verify_frame_bytes(frame[: frame[1]]):
        return None
    rssi_raw = frame[5]
    rssi = rssi_raw - 256 if rssi_raw >= 128 else rssi_raw
    fw_int = struct.unpack("<I", bytes(frame[6:10]))[0]
    return {
        "rssi": rssi,
        "firmware_version": f"{fw_int:08X}",
    }


def build_manual_light_frame(
    on: bool,
    red: int = const.DEFAULT_TEMP_RED,
    white: int = const.DEFAULT_TEMP_WHITE,
    green: int = const.DEFAULT_TEMP_GREEN,
    blue: int = const.DEFAULT_TEMP_BLUE,
    fan: int | None = None,
    *,
    mode_id: int = 12,
    grade: int = 0,
    restore_act_time: int = 1000,
    enforce_safety: bool = True,
) -> str:
    """Build 0x10 persistent manual main-light frame (23 bytes: [state, mode=1, 0, 0, R, W, G, B, F, act_le, modeId, grade_le, 0, 0])."""
    if not on:
        r = w = g = b = f_pwm = m_id = grd = 0
        act = 0
    else:
        if enforce_safety:
            r, w, g, b, f_pwm = presets.enforce_power_and_fan_safety(red, white, green, blue, fan)
        else:
            r = max(0, min(255, int(red)))
            w = max(0, min(255, int(white)))
            g = max(0, min(255, int(green)))
            b = max(0, min(255, int(blue)))
            f_pwm = presets.fan_gear_to_pwm(fan) if (fan is not None and 0 < fan <= 10) else max(0, min(255, int(fan or 0)))
        m_id = mode_id & 0xFF
        grd = grade & 0xFFFF
        act = max(0, min(65535, int(restore_act_time)))
    payload = [
        1 if on else 0,
        1,  # lampMode: 1 = manual persistent control
        0,
        0,
        r & 0xFF,
        w & 0xFF,
        g & 0xFF,
        b & 0xFF,
        f_pwm & 0xFF,
        act & 0xFF,
        (act >> 8) & 0xFF,
        m_id,
        grd & 0xFF,
        (grd >> 8) & 0xFF,
        0,
        0,
    ]
    return build_app_frame(0x01, 0x10, payload)


def build_temp_light_frame(
    on: bool,
    red: int = const.DEFAULT_TEMP_RED,
    white: int = const.DEFAULT_TEMP_WHITE,
    green: int = const.DEFAULT_TEMP_GREEN,
    blue: int = const.DEFAULT_TEMP_BLUE,
    fan: int | None = const.DEFAULT_TEMP_FAN,
    *,
    on_minutes: int | None = None,
    off_minutes: int | None = None,
    on_seconds: int | None = None,
    off_seconds: int | None = None,
    mode_id: int = const.DEFAULT_TEMP_MODE_ID,
    grade: int = const.DEFAULT_TEMP_GRADE,
    enforce_safety: bool = True,
) -> str:
    """Build the 0x1A temporary viewing light on/off frame (duration unit = minutes on firmware 0000006F)."""
    # Preserve exact parameter precedence while supporting legacy on_seconds/off_seconds callers
    on_dur = on_minutes if on_minutes is not None else (on_seconds if on_seconds is not None else const.DEFAULT_TEMP_ON_MINUTES)
    off_dur = off_minutes if off_minutes is not None else (off_seconds if off_seconds is not None else const.DEFAULT_TEMP_OFF_MINUTES)
    on_dur = clamp_int(on_dur, const.DEFAULT_TEMP_ON_MINUTES, const.MIN_TEMP_MINUTES, const.MAX_TEMP_MINUTES)
    off_dur = clamp_int(off_dur, const.DEFAULT_TEMP_OFF_MINUTES, const.MIN_TEMP_MINUTES, const.MAX_TEMP_MINUTES)
    if on:
        if enforce_safety:
            r, w, g, b, f_pwm = presets.enforce_power_and_fan_safety(red, white, green, blue, fan)
        else:
            r = clamp_int(red, const.DEFAULT_TEMP_RED, 0, 255)
            w = clamp_int(white, const.DEFAULT_TEMP_WHITE, 0, 255)
            g = clamp_int(green, const.DEFAULT_TEMP_GREEN, 0, 255)
            b = clamp_int(blue, const.DEFAULT_TEMP_BLUE, 0, 255)
            f_pwm = clamp_int(fan, 0, 0, 255)
    else:
        r = w = g = b = f_pwm = mode_id = grade = 0
    return build_app_frame(
        0x01,
        0x1A,
        [
            on_dur & 0xFF,
            (on_dur >> 8) & 0xFF,
            off_dur & 0xFF,
            (off_dur >> 8) & 0xFF,
            r & 0xFF,
            w & 0xFF,
            g & 0xFF,
            b & 0xFF,
            f_pwm & 0xFF,
            mode_id & 0xFF,
            grade & 0xFF,
            (grade >> 8) & 0xFF,
        ],
    )


def build_query_state_frame() -> str:
    """Build 0x15 active query frame to request 0x14 live state."""
    return build_app_frame(0x01, 0x15, [0])


def build_query_temp_0x1b() -> str:
    """Build 0x1B query frame to request stored 0x1A temporary light config."""
    return build_app_frame(0x01, 0x1B, [0])


def build_query_schedule_0x25(mask: int = 1) -> str:
    """Build 0x25 schedule query frame (bitmask: 1=master, 2=spot, 4=atmosphere)."""
    return build_app_frame(0x01, 0x25, [mask & 0xFF])


def build_atmosphere_manual_0x1e(on: bool, pwm: int, grade: int = 0, *, restore_act_time: int = 1000) -> str:
    """Build 0x1E atmosphere manual light control frame (9B payload: [state, mode=1, 0, 0, pwm, act_le, grade, 0])."""
    val = max(0, min(255, int(pwm))) if on else 0
    grd = max(0, min(5, int(grade))) if on else 0
    act = max(0, min(65535, int(restore_act_time))) if on else 0
    return build_app_frame(0x01, 0x1E, [1 if on else 0, 1, 0, 0, val, act & 0xFF, (act >> 8) & 0xFF, grd, 0])


def build_atmosphere_follow_0x22(follow_type: int, pwm: int, grade: int = 0) -> str:
    """Build 0x22 atmosphere follow-mode & brightness frame (3B payload: [followType, pwm, grade])."""
    ft = max(0, min(2, int(follow_type)))
    val = max(0, min(255, int(pwm)))
    grd = max(0, min(5, int(grade)))
    return build_app_frame(0x01, 0x22, [ft, val, grd])


def build_voice_config_0x17(
    voice_switch: bool,
    *,
    power_play: bool = True,
    prompts: tuple[int, ...] | list[int] | None = None,
) -> str:
    """Build 0x17 offline voice config frame (11 payload bytes)."""
    p_vals = list(prompts) if prompts is not None else [1] * 9
    p_vals = (p_vals + [1] * 9)[:9]
    payload = [1 if voice_switch else 0, 1 if power_play else 0, *(1 if x else 0 for x in p_vals)]
    return build_app_frame(0x01, 0x17, payload)


def _day_list_to_bitmask(days: list[Any] | None) -> int:
    if not isinstance(days, list) or len(days) < 7:
        return 0x7F
    mask = 0
    for idx in range(7):
        if as_bool(days[idx]) is not False:
            mask |= 1 << idx
    return mask


def _bitmask_to_day_list(mask: int) -> list[int]:
    return [1 if (mask & (1 << idx)) else 0 for idx in range(7)]


def build_schedule_frame_0x12(
    trips: list[dict[str, Any]],
    *,
    sunrise_minutes: int = 0,
    sunset_minutes: int = 0,
    enable_schedule: bool = True,
) -> str:
    """Build 152-byte 0x12 master 24-hour schedule frame (up to 10 slots)."""
    sr = clamp_int(sunrise_minutes, 0, 0, 180)
    ss = clamp_int(sunset_minutes, 0, 0, 180)
    payload: list[int] = [1 if enable_schedule else 0, sr & 0xFF, (sr >> 8) & 0xFF, ss & 0xFF, (ss >> 8) & 0xFF]
    slots = trips[:10]
    for idx in range(10):
        if idx < len(slots):
            t = slots[idx]
            en = 1 if as_bool(t.get("enable", True)) is not False else 0
            day_mask = _day_list_to_bitmask(t.get("dayList"))
            sh = clamp_int(t.get("startH", t.get("start_hour", 0)), 0, 0, 23)
            sm = clamp_int(t.get("startM", t.get("start_minute", 0)), 0, 0, 59)
            eh = clamp_int(t.get("endH", t.get("end_hour", 0)), 0, 0, 23)
            em = clamp_int(t.get("endM", t.get("end_minute", 0)), 0, 0, 59)
            r, w, g, b, f_pwm = presets.enforce_power_and_fan_safety(
                as_int(t.get("R", t.get("red")), 0) or 0,
                as_int(t.get("W", t.get("white")), 0) or 0,
                as_int(t.get("G", t.get("green")), 0) or 0,
                as_int(t.get("B", t.get("blue")), 0) or 0,
                as_int(t.get("F", t.get("fan")), None),
            )
            mode_id = clamp_int(t.get("mode", t.get("mode_id", 1)), 1, 0, 255)
            grade = clamp_int(t.get("grade", 0), 0, 0, 65535)
            payload.extend(
                [en, day_mask, sh, sm, eh, em, r, w, g, b, f_pwm, mode_id, grade & 0xFF, (grade >> 8) & 0xFF]
            )
        else:
            payload.extend([0] * 14)
    return build_app_frame(0x01, 0x12, payload)


def decode_schedule_0x12(message_hex: str | None, model: str | None = None) -> dict[str, Any] | None:
    """Decode 152-byte 0x12 master schedule frame into structured trips + sunrise/sunset."""
    frame = hex_bytes(message_hex)
    if len(frame) < 152 or frame[0] != 0x5A or frame[2] != 0x01 or frame[3] != 0x12:
        return None
    if not verify_frame_bytes(frame[:152]):
        return None
    p = frame[6:151]  # 145 bytes: 5 header + 10 * 14 slots
    enabled = bool(p[0])
    sunrise_min = p[1] | (p[2] << 8)
    sunset_min = p[3] | (p[4] << 8)
    trips: list[dict[str, Any]] = []
    for idx in range(10):
        slot = p[5 + idx * 14 : 5 + (idx + 1) * 14]
        if all(x == 0 for x in slot):
            continue
        en, day_mask, sh, sm, eh, em, r, w, g, b, f_pwm, mode_id, grd_lo, grd_hi = slot
        grade = grd_lo | (grd_hi << 8)
        matched = presets.match_preset_from_channels(model, r, w, g, b)
        trip_name = f"{matched[0]} {matched[1]}档" if matched else f"时段{idx + 1} ({sh:02d}:{sm:02d}-{eh:02d}:{em:02d})"
        trips.append(
            {
                "slot": idx,
                "name": trip_name,
                "enable": en,
                "dayList": _bitmask_to_day_list(day_mask),
                "startH": sh,
                "startM": sm,
                "endH": eh,
                "endM": em,
                "R": r,
                "W": w,
                "G": g,
                "B": b,
                "F": f_pwm,
                "mode": mode_id,
                "grade": grade,
            }
        )
    return {
        "enabled": enabled,
        "sunrise_minutes": sunrise_min,
        "sunset_minutes": sunset_min,
        "trips": trips,
    }


def decode_temp_0x1a(message_hex: str | None) -> dict[str, Any] | None:
    """Decode 0x1A temporary viewing light configuration reply."""
    frame = hex_bytes(message_hex)
    if len(frame) < 19 or frame[0] != 0x5A or frame[2] != 0x01 or frame[3] != 0x1A:
        return None
    if not verify_frame_bytes(frame[:19]):
        return None
    p = frame[6:18]
    return {
        "on_minutes": p[0] | (p[1] << 8),
        "off_minutes": p[2] | (p[3] << 8),
        "R": p[4],
        "W": p[5],
        "G": p[6],
        "B": p[7],
        "F": p[8],
        "mode_id": p[9],
        "grade": p[10] | (p[11] << 8),
    }


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
    status = parse_status_log(row)
    master = status.get("_live_master") if isinstance(status.get("_live_master"), dict) else None
    if is_main_travel_running(row):
        name = current_trip_name(row, now)
        return f"日程中（{name}）" if name else "日程中"
    if isinstance(master, dict) and master.get("pattern") == 1 and master.get("lampState") == 1:
        return "手动常亮"
    is_on = is_main_light_on(row)
    if is_on is True:
        return "临时开灯"
    if is_on is False:
        return "临时关灯" if has_main_travel(row) else "关灯"
    return "未知"


def online_lamp_count(row: dict[str, Any]) -> int:
    """Return count of online lamps reported for the device group."""
    status = parse_status_log(row)
    if "_online_count" in status and status["_online_count"] is not None:
        return int(status["_online_count"])
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
    """Collect unique uppercase realAddress values from replies."""
    seen: dict[str, None] = {}
    for body in _reply_bodies(replies):
        if addr := str(body.get("realAddress") or "").strip().upper():
            seen.setdefault(addr, None)
    return list(seen)


def decode_lamps_state_reply(message_hex: str | None) -> dict[str, Any] | None:
    """Decode one 0x14 lamp state reply frame (real hardware order: R, W, G, B, F)."""
    frame = hex_bytes(message_hex)
    if len(frame) < 38 or frame[0] != 0x5A or frame[2] != 0x01 or frame[3] != 0x14:
        return None
    if not verify_frame_bytes(frame[:38]):
        return None
    payload = frame[6:37]
    if len(payload) < 31:
        return None
    return {
        "master": {k: int(payload[i]) for i, k in enumerate(_MASTER_KEYS)},
        "spot": {k: int(payload[13 + i]) for i, k in enumerate(_SMALL_KEYS)},
        "atmosphere": {k: int(payload[22 + i]) for i, k in enumerate(_SMALL_KEYS)},
    }


def lamps_state_from_replies(replies: list[Any]) -> dict[str, Any] | None:
    """Return the first decoded 0x14 lamp state from replies."""
    for body in _reply_bodies(replies):
        if parsed := decode_lamps_state_reply(body.get("message")):
            return parsed
    return None


def live_master_channels(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """Return live (R, W, G, B, F_pwm) tuple from statusLog."""
    status = parse_status_log(row)
    master = status.get("_live_master")
    if isinstance(master, dict):
        return (
            int(master.get("R", 0)),
            int(master.get("W", 0)),
            int(master.get("G", 0)),
            int(master.get("B", 0)),
            int(master.get("F", 0)),
        )
    return (0, 0, 0, 0, 0)


def live_atmosphere_state(row: dict[str, Any]) -> dict[str, int]:
    """Return live atmosphere dict {lampState, moodLamp, followType, pattern}."""
    status = parse_status_log(row)
    atmos = status.get("_live_atmosphere")
    if isinstance(atmos, dict):
        return {
            "lampState": int(atmos.get("lampState", 0)),
            "moodLamp": int(atmos.get("moodLamp", 0)),
            "followType": int(atmos.get("followType", 0)),
            "pattern": int(atmos.get("pattern", 0)),
        }
    return {"lampState": 0, "moodLamp": 0, "followType": 0, "pattern": 0}


def apply_decoded_state_to_status(
    status: dict[str, Any],
    state_0x14: dict[str, Any] | None = None,
    *,
    schedule_0x12: dict[str, Any] | None = None,
    heartbeat_0x0a: dict[str, Any] | None = None,
    online_addresses: list[str] | None = None,
) -> dict[str, Any]:
    """Merge live 0x14 / 0x12 / 0x0A decoded frames into statusLog dict (in-place & returned)."""
    if isinstance(state_0x14, dict):
        master = state_0x14.get("master")
        if isinstance(master, dict):
            pattern = int(master.get("pattern") or 0)
            status["onoff"] = int(master.get("lampState") or 0)
            status["travelRunning"] = {
                **(status.get("travelRunning") if isinstance(status.get("travelRunning"), dict) else {}),
                "main": pattern == 2,
            }
            status["manualStatus"] = {
                **(status.get("manualStatus") if isinstance(status.get("manualStatus"), dict) else {}),
                "main": pattern in (1, 3),
            }
            status["_live_master"] = dict(master)
        atmos = state_0x14.get("atmosphere")
        if isinstance(atmos, dict):
            status["_live_atmosphere"] = dict(atmos)
    if isinstance(schedule_0x12, dict):
        trips = schedule_0x12.get("trips") or []
        status["haveTravel"] = {
            **(status.get("haveTravel") if isinstance(status.get("haveTravel"), dict) else {}),
            "main": bool(trips) and bool(schedule_0x12.get("enabled", True)),
        }
        status["masterhand"] = {
            **(status.get("masterhand") if isinstance(status.get("masterhand"), dict) else {}),
            "travelList": trips,
            "sunriseMinutes": int(schedule_0x12.get("sunrise_minutes") or 0),
            "sunsetMinutes": int(schedule_0x12.get("sunset_minutes") or 0),
        }
    if isinstance(heartbeat_0x0a, dict):
        if "rssi" in heartbeat_0x0a:
            status["_wifi_rssi"] = int(heartbeat_0x0a["rssi"])
        if heartbeat_0x0a.get("firmware_version"):
            status["_firmware_version"] = str(heartbeat_0x0a["firmware_version"])
    if online_addresses is not None:
        status["onLineRealAddress"] = ",".join(online_addresses)
        status["_online_count"] = len(online_addresses)
    return status
