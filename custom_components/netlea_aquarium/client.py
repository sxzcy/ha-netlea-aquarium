"""Async Netlea cloud REST and WebSocket client."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import sys
import time
from typing import Any
from urllib.parse import urlparse

import aiohttp

from . import const, protocol as proto

_TRANSIENT_API_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
_AUTH_API_CODES = frozenset({401, 403, 409})
_MAX_WS_FRAMES = 64
_MAX_WS_PAYLOAD_BYTES = 65536


class NetleaError(Exception):
    """Base Netlea error."""


class NetleaAuthError(NetleaError):
    """Authentication or session error."""


class NetleaConnectionError(NetleaError):
    """Temporary network or transport error."""


class NetleaControlAckError(NetleaConnectionError):
    """Control command did not receive a valid device acknowledgement."""


def normalize_legacy_ws_url(ws_url: str | None) -> str:
    """Upgrade the legacy plaintext default Netlea WebSocket URL to TLS."""
    cleaned = str(ws_url or "").strip()
    if not cleaned or cleaned.rstrip("/").lower() == const.LEGACY_WS_URL.rstrip("/").lower():
        return const.WS_URL
    return cleaned


def _has_scheme(url: str | None, scheme: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    return parsed.scheme.lower() == scheme and bool(parsed.netloc)


def is_valid_api_base(url: str | None) -> bool:
    """Return whether the API base URL uses https:// with a valid host."""
    return _has_scheme(url, "https")


def is_valid_ws_url(url: str | None) -> bool:
    """Return whether the WebSocket URL uses wss:// with a valid host."""
    return _has_scheme(url, "wss")


def _err_detail(err: BaseException | None) -> str:
    return (str(err) or err.__class__.__name__) if err is not None else "unknown error"


def _safe_msg(msg: str, token: str | None) -> str:
    cleaned = " ".join(msg.split()).strip()
    return (cleaned.replace(token, "<redacted>") if token and token in cleaned else cleaned)[:200]


def _api_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Accept-Language": "zh-CN",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 NetleaLocalCLI",
    }
    if token:
        headers["Authorization"] = token
    return headers


async def _sleep_before_retry(attempt: int) -> None:
    await asyncio.sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))


class NetleaClient:
    """Async Netlea cloud client using Home Assistant's aiohttp session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        api_base: str = const.API_BASE,
        ws_url: str = const.WS_URL,
        token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        normalized_api = str(api_base or const.API_BASE).strip().rstrip("/")
        normalized_ws = normalize_legacy_ws_url(ws_url)
        if not is_valid_api_base(normalized_api):
            raise NetleaError("API base URL must use https://")
        if not is_valid_ws_url(normalized_ws):
            raise NetleaError("WebSocket URL must use wss://")
        self._session = session
        self._control_lock = asyncio.Lock()
        self.api_base, self.ws_url, self.token, self.user_id = normalized_api, normalized_ws, token, user_id

    @classmethod
    def from_config_entry_data(cls, session: aiohttp.ClientSession, data: dict[str, Any]) -> NetleaClient:
        """Create a client from config entry data."""
        return cls(
            session,
            api_base=str(data.get(const.CONF_API_BASE) or const.API_BASE),
            ws_url=normalize_legacy_ws_url(data.get(const.CONF_WS_URL)),
            token=str(data.get(const.CONF_TOKEN) or ""),
            user_id=str(data.get(const.CONF_USER_ID) or ""),
        )

    async def _request_json_once(
        self,
        method: str,
        url: str,
        token: str | None,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | None,
    ) -> Any:
        timeout = aiohttp.ClientTimeout(total=const.DEFAULT_REQUEST_TIMEOUT)
        async with self._session.request(
            method, url, params=params, json=payload, headers=_api_headers(token), timeout=timeout
        ) as response:
            status, text = response.status, await response.text()
        if status in (401, 403):
            raise NetleaAuthError(f"Netlea authentication failed (HTTP {status})")
        if status == 429 or 500 <= status < 600:
            raise NetleaConnectionError(f"Netlea cloud transient HTTP {status}")
        if status >= 400:
            raise NetleaError(f"Netlea cloud HTTP {status}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as err:
            raise NetleaError("Netlea cloud returned invalid JSON") from err
        if isinstance(data, dict):
            raw_code = data.get("code")
            code = proto.as_int(raw_code)
            msg = _safe_msg(str(data.get("msg") or ""), token)
            if code in _AUTH_API_CODES:
                raise NetleaAuthError(f"Netlea authentication failed (code {code})")
            if code in _TRANSIENT_API_CODES:
                raise NetleaConnectionError(msg or f"Netlea API transient error (code {code})")
            if raw_code not in (None, "") and code != 200:
                raise NetleaError(msg or f"Netlea API error (code {raw_code})")
        return data

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, const.DEFAULT_REQUEST_RETRIES + 1):
            try:
                return await self._request_json_once(method, url, token, params, payload)
            except (NetleaConnectionError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                last_error = err
                if attempt >= const.DEFAULT_REQUEST_RETRIES:
                    if isinstance(err, NetleaConnectionError):
                        raise
                    raise NetleaConnectionError(
                        f"Netlea cloud request failed after {attempt} attempts: {_err_detail(err)}"
                    ) from err
                await _sleep_before_retry(attempt)
        raise NetleaConnectionError(f"Netlea cloud request failed: {_err_detail(last_error)}")

    async def async_send_code(self, phone: str) -> str:
        """Send an SMS login verification code."""
        data = await self._request_json(
            "GET", f"{self.api_base}/V3/Api/captchaMsg", params={"captchaMsgFlag": "3", "phonenumber": phone}
        )
        if not (uuid := proto.extract(data, "uuid", "data.uuid")):
            raise NetleaError("Netlea SMS code response missing uuid")
        return str(uuid)

    async def async_login(self, phone: str, code: str, uuid: str) -> dict[str, Any]:
        """Log in with an SMS code and fetch associated device groups."""
        data = await self._request_json(
            "POST", f"{self.api_base}/V3/Api/appuser/loginBycode", payload={"username": phone, "code": code, "uuid": uuid}
        )
        token, user_id = proto.extract(data, "token", "data.token"), proto.extract(data, "userId", "data.userId")
        if not token or user_id in (None, ""):
            raise NetleaAuthError("Netlea login response missing token or userId")
        self.token, self.user_id = str(token), str(user_id)
        return {"token": self.token, "user_id": self.user_id, "devices": await self.async_fetch_devices()}

    async def async_fetch_devices(self) -> list[dict[str, Any]]:
        """Fetch normalized device rows from Netlea cloud."""
        if not self.token or not self.user_id:
            raise NetleaAuthError("Missing Netlea token or user_id")
        data = await self._request_json(
            "POST",
            f"{self.api_base}/V3/Api/batchSyncEquipmentUser",
            token=self.token,
            payload={"userId": self.user_id, "client": 0, "equipmentUserEntityList": []},
        )
        rows = data.get("rows") if isinstance(data, dict) else None
        return [proto.normalize_device_row(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def _ws_connect_kwargs(self, connect_timeout: float, reply_timeout: float) -> dict[str, Any]:
        ws_timeout_cls = getattr(aiohttp, "ClientWSTimeout", None)
        timeout = (
            ws_timeout_cls(ws_receive=reply_timeout, ws_close=const.DEFAULT_WS_CLOSE_TIMEOUT)
            if ws_timeout_cls is not None
            else connect_timeout
        )
        return {"headers": {"token": self.token}, "heartbeat": 20, "timeout": timeout}

    async def async_send_frame(
        self,
        target_address: str,
        frame: str,
        timeout: float = const.DEFAULT_CONTROL_REPLY_TIMEOUT,
        *,
        connect_timeout: float = const.DEFAULT_CONTROL_CONNECT_TIMEOUT,
        retries: int = const.DEFAULT_CONTROL_RETRIES,
    ) -> proto.ControlResult:
        """Send one WebSocket control frame with bounded retries."""
        if not self.token:
            raise NetleaAuthError("Missing Netlea token")
        max_attempts = max(1, min(int(retries), const.DEFAULT_CONTROL_RETRIES))
        last_error: Exception | None = None
        async with self._control_lock:
            for attempt in range(1, max_attempts + 1):
                try:
                    return await self._send_frame_once(target_address, frame, timeout, connect_timeout)
                except NetleaAuthError:
                    raise
                except aiohttp.ClientResponseError as err:
                    if err.status in (401, 403):
                        raise NetleaAuthError(f"Netlea WebSocket authentication failed (HTTP {err.status})") from err
                    last_error = err
                except (NetleaConnectionError, aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
                    last_error = err
                if attempt >= max_attempts:
                    raise NetleaConnectionError(
                        f"Netlea WebSocket request failed after {attempt} attempts: {_err_detail(last_error)}"
                    ) from last_error
                await _sleep_before_retry(attempt)
        raise NetleaConnectionError(f"Netlea WebSocket request failed: {_err_detail(last_error)}")

    async def _send_frame_once(
        self, target_address: str, frame: str, timeout: float, connect_timeout: float
    ) -> proto.ControlResult:
        expected = proto.split_addresses(target_address)
        if not expected:
            raise NetleaError("Missing Netlea control address")
        expected_set, normalized_target = set(expected), ",".join(expected)
        ws_cm = self._session.ws_connect(self.ws_url, **self._ws_connect_kwargs(connect_timeout, timeout))
        ws = await asyncio.wait_for(ws_cm.__aenter__(), timeout=connect_timeout)

        replies: list[Any] = []
        exc_info: tuple[Any, Any, Any] = (None, None, None)
        try:
            payload = {
                "msgType": 1002,
                "msgBody": {
                    "time": int(time.time() * 1000),
                    "all": "1",
                    "topic": f"App/Control/{normalized_target}",
                    "message": frame,
                },
            }
            await ws.send_str(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            for _ in range(_MAX_WS_FRAMES):
                if (remaining := deadline - loop.time()) <= 0:
                    break
                try:
                    incoming = await ws.receive(timeout=max(0.1, remaining))
                except asyncio.TimeoutError:
                    break
                if incoming.type == aiohttp.WSMsgType.TEXT:
                    raw = incoming.data
                elif incoming.type == aiohttp.WSMsgType.BINARY:
                    raw = incoming.data.decode("utf-8", errors="ignore")
                elif incoming.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                    raise NetleaConnectionError("Netlea WebSocket closed before device reply")
                elif incoming.type == aiohttp.WSMsgType.ERROR:
                    raise NetleaConnectionError(f"Netlea WebSocket error: {_err_detail(ws.exception())}")
                else:
                    continue
                if len(raw) > _MAX_WS_PAYLOAD_BYTES:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and (code := proto.as_int(parsed.get("code"))) in _AUTH_API_CODES:
                    raise NetleaAuthError(f"Netlea WebSocket authentication failed (code {code})")
                addrs = proto.reply_real_addresses([parsed])
                if addrs and not any(addr in expected_set for addr in addrs):
                    continue
                replies.append(parsed)
                if expected_set.issubset(set(proto.reply_real_addresses(replies))):
                    break
        except BaseException:
            exc_info = sys.exc_info()
            raise
        finally:
            await ws_cm.__aexit__(*exc_info)

        replied = proto.reply_real_addresses(replies)
        if not replied:
            raise NetleaControlAckError(f"Netlea WebSocket command received no reply for {normalized_target}")
        return proto.ControlResult(
            frame=frame,
            replies=replies,
            expected_addresses=expected,
            replied_addresses=replied,
            missing_addresses=[addr for addr in expected if addr not in replied],
            legacy_state=proto.lamps_state_from_replies(replies),
        )

    async def async_temp_on(
        self,
        target_address: str,
        *,
        red: int = const.DEFAULT_TEMP_RED,
        white: int = const.DEFAULT_TEMP_WHITE,
        green: int = const.DEFAULT_TEMP_GREEN,
        blue: int = const.DEFAULT_TEMP_BLUE,
        fan: int = const.DEFAULT_TEMP_FAN,
    ) -> proto.ControlResult:
        """Temporarily turn on the main viewing light with configured channels."""
        return await self.async_send_frame(
            target_address, proto.build_temp_light_frame(True, red=red, white=white, green=green, blue=blue, fan=fan)
        )

    async def async_temp_off(self, target_address: str) -> proto.ControlResult:
        """Temporarily turn off the main light."""
        return await self.async_send_frame(target_address, proto.build_temp_light_frame(False))

    async def async_resume_schedule(
        self, target_address: str, status_log: dict[str, Any] | None, *, now: datetime | None = None
    ) -> proto.ControlResult:
        """Resume the configured main-light schedule."""
        return await self.async_send_frame(target_address, proto.build_resume_schedule_frame(status_log, now))
