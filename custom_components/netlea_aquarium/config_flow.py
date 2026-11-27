"""Config and options flow for Netlea Aquarium (v2.0.0 — Local MQTT / Local BLE / Cloud WS)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import const, protocol as proto
from .client import NetleaAuthError, NetleaClient, NetleaError, is_valid_api_base, is_valid_ws_url, normalize_legacy_ws_url
from .transport import parse_aes_key

_LOGGER = logging.getLogger(__name__)

_OPTION_SPECS: tuple[tuple[str, int, int, int], ...] = (
    (const.CONF_SCAN_INTERVAL, const.DEFAULT_SCAN_INTERVAL, const.MIN_SCAN_INTERVAL, const.MAX_SCAN_INTERVAL),
    (const.CONF_STALE_FAILURES, const.DEFAULT_STALE_FAILURES, const.MIN_STALE_FAILURES, const.MAX_STALE_FAILURES),
    (const.CONF_TEMP_ON_MINUTES, const.DEFAULT_TEMP_ON_MINUTES, const.MIN_TEMP_MINUTES, const.MAX_TEMP_MINUTES),
    (const.CONF_TEMP_RED, const.DEFAULT_TEMP_RED, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    (const.CONF_TEMP_WHITE, const.DEFAULT_TEMP_WHITE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    (const.CONF_TEMP_GREEN, const.DEFAULT_TEMP_GREEN, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    (const.CONF_TEMP_BLUE, const.DEFAULT_TEMP_BLUE, const.MIN_TEMP_CHANNEL, const.MAX_TEMP_CHANNEL),
    (const.CONF_TEMP_FAN, const.DEFAULT_TEMP_FAN, const.MIN_TEMP_FAN, const.MAX_TEMP_FAN),
)


def _normalize_phone(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[2:] if digits.startswith("86") and len(digits) == 13 else digits


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if not text or text.lower() == "none" else text


def _device_key(device: dict[str, Any]) -> str | None:
    return (
        _clean_text(device.get("equipmentUserId"))
        or _clean_text(device.get("controlRealAddress"))
        or _clean_text(device.get("realAddress"))
    )


def _device_label(device: dict[str, Any]) -> str:
    ownership = "共享" if proto.as_int(device.get("shareFlag"), 0) == 2 else "自有"
    return f"{device.get('name') or 'Netlea'} | {ownership} | {device.get('controlRealAddress') or device.get('realAddress') or ''}"


def _device_matches_entry(entry_data: Mapping[str, Any], device: dict[str, Any]) -> bool:
    entry_ids = {
        v
        for v in (
            _clean_text(entry_data.get(const.CONF_EQUIPMENT_USER_ID)),
            _clean_text(entry_data.get(const.CONF_CONTROL_REAL_ADDRESS)),
        )
        if v
    }
    device_ids = {
        v
        for v in (
            _device_key(device),
            _clean_text(device.get("controlRealAddress")),
            _clean_text(device.get("realAddress")),
        )
        if v
    }
    return bool(entry_ids & device_ids)


class NetleaConfigFlow(config_entries.ConfigFlow, domain=const.DOMAIN):
    """Handle a config flow for Netlea Aquarium."""

    VERSION = 2

    def __init__(self) -> None:
        self._phone: str | None = None
        self._uuid: str | None = None
        self._token: str | None = None
        self._user_id: str | None = None
        self._api_base = const.API_BASE
        self._ws_url = const.WS_URL
        self._devices: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    def _client(self) -> NetleaClient:
        return NetleaClient(async_get_clientsession(self.hass), api_base=self._api_base, ws_url=self._ws_url)

    async def _send_sms(self, phone: str, errors: dict[str, str]) -> str:
        try:
            self._uuid = await self._client().async_send_code(phone)
            return ""
        except NetleaError as err:
            _LOGGER.warning("Failed to send Netlea SMS code: %s", err)
            errors["base"] = "send_code_failed"
            text = " ".join(str(err).split()).strip()
            return f"\n\n错误详情：{text[:160]}" if text else ""

    async def _login_with_code(self, code: str, errors: dict[str, str]) -> dict[str, Any] | None:
        try:
            return await self._client().async_login(str(self._phone), code.strip(), str(self._uuid))
        except NetleaAuthError:
            errors["base"] = "invalid_auth"
        except NetleaError as err:
            _LOGGER.warning("Failed to login to Netlea: %s", err)
            errors["base"] = "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Choose transport mode (Local MQTT / Local BLE / Cloud WS); supports direct cloud input for legacy callers."""
        if user_input is not None:
            if const.CONF_PHONE in user_input:
                return await self.async_step_cloud(user_input)
            mode = str(user_input.get(const.CONF_TRANSPORT_MODE) or const.TRANSPORT_LOCAL_MQTT)
            if mode == const.TRANSPORT_LOCAL_MQTT:
                return await self.async_step_local_mqtt()
            if mode == const.TRANSPORT_LOCAL_BLE:
                return await self.async_step_local_ble()
            return await self.async_step_cloud()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(const.CONF_TRANSPORT_MODE, default=const.TRANSPORT_LOCAL_MQTT): vol.In(
                        {
                            const.TRANSPORT_LOCAL_MQTT: "Local MQTT（内置本地加密服务端，推荐）",
                            const.TRANSPORT_LOCAL_BLE: "Local BLE（本机蓝牙直连，免密钥）",
                            const.TRANSPORT_CLOUD_WS: "Cloud WebSocket（尼特利官方云端）",
                        }
                    )
                }
            ),
        )

    async def async_step_local_mqtt(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Configure Local MQTT embedded server mode (no cloud account required)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            addrs = proto.split_addresses(user_input.get(const.CONF_CONTROL_REAL_ADDRESS))
            if not addrs or any(len(a) != 12 for a in addrs):
                errors[const.CONF_CONTROL_REAL_ADDRESS] = "invalid_mac"
            aes_key = str(user_input.get(const.CONF_MQTT_AES_KEY) or "").strip()
            try:
                parse_aes_key(aes_key)
            except NetleaError:
                errors[const.CONF_MQTT_AES_KEY] = "invalid_aes_key"
            if not errors:
                normalized_addrs = ",".join(addrs)
                await self.async_set_unique_id(normalized_addrs)
                self._abort_if_unique_id_configured()
                device_name = str(user_input.get(const.CONF_DEVICE_NAME) or "尼特利鱼缸").strip()
                model = str(user_input.get(const.CONF_MODEL) or const.DEFAULT_MODEL)
                port = proto.clamp_int(
                    user_input.get(const.CONF_MQTT_PORT),
                    const.DEFAULT_MQTT_PORT,
                    const.MIN_MQTT_PORT,
                    const.MAX_MQTT_PORT,
                )
                return self.async_create_entry(
                    title=device_name,
                    data={
                        const.CONF_TRANSPORT_MODE: const.TRANSPORT_LOCAL_MQTT,
                        const.CONF_DEVICE_NAME: device_name,
                        const.CONF_MODEL: model,
                        const.CONF_CONTROL_REAL_ADDRESS: normalized_addrs,
                        const.CONF_MQTT_PORT: port,
                        const.CONF_MQTT_AES_KEY: aes_key.upper(),
                    },
                )

        return self.async_show_form(
            step_id="local_mqtt",
            data_schema=vol.Schema(
                {
                    vol.Required(const.CONF_DEVICE_NAME, default="尼特利鱼缸"): str,
                    vol.Required(const.CONF_MODEL, default=const.DEFAULT_MODEL): vol.In(const.SUPPORTED_MODELS),
                    vol.Required(const.CONF_CONTROL_REAL_ADDRESS): str,
                    vol.Required(const.CONF_MQTT_PORT, default=const.DEFAULT_MQTT_PORT): vol.All(
                        vol.Coerce(int), vol.Range(min=const.MIN_MQTT_PORT, max=const.MAX_MQTT_PORT)
                    ),
                    vol.Required(const.CONF_MQTT_AES_KEY): str,
                }
            ),
            errors=errors,
        )

    async def async_step_local_ble(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Configure Local BLE direct mode (no cloud account or key required)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            addrs = proto.split_addresses(user_input.get(const.CONF_CONTROL_REAL_ADDRESS))
            if not addrs or any(len(a) != 12 for a in addrs):
                errors[const.CONF_CONTROL_REAL_ADDRESS] = "invalid_mac"
            if not errors:
                normalized_addrs = ",".join(addrs)
                await self.async_set_unique_id(normalized_addrs)
                self._abort_if_unique_id_configured()
                device_name = str(user_input.get(const.CONF_DEVICE_NAME) or "尼特利鱼缸").strip()
                model = str(user_input.get(const.CONF_MODEL) or const.DEFAULT_MODEL)
                return self.async_create_entry(
                    title=device_name,
                    data={
                        const.CONF_TRANSPORT_MODE: const.TRANSPORT_LOCAL_BLE,
                        const.CONF_DEVICE_NAME: device_name,
                        const.CONF_MODEL: model,
                        const.CONF_CONTROL_REAL_ADDRESS: normalized_addrs,
                    },
                )

        return self.async_show_form(
            step_id="local_ble",
            data_schema=vol.Schema(
                {
                    vol.Required(const.CONF_DEVICE_NAME, default="尼特利鱼缸"): str,
                    vol.Required(const.CONF_MODEL, default=const.DEFAULT_MODEL): vol.In(const.SUPPORTED_MODELS),
                    vol.Required(const.CONF_CONTROL_REAL_ADDRESS): str,
                }
            ),
            errors=errors,
        )

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Validate cloud endpoints and send an SMS login code."""
        errors: dict[str, str] = {}
        details = ""
        if user_input is not None:
            phone = _normalize_phone(user_input.get(const.CONF_PHONE))
            api_base = str(user_input.get(const.CONF_API_BASE) or const.API_BASE).strip()
            ws_url = str(user_input.get(const.CONF_WS_URL) or const.WS_URL).strip()
            self._phone, self._api_base, self._ws_url = phone, api_base, ws_url
            if not (len(phone) == 11 and phone.startswith("1")):
                errors[const.CONF_PHONE] = "invalid_phone"
            if not is_valid_api_base(api_base):
                errors[const.CONF_API_BASE] = "invalid_api_base"
            if not is_valid_ws_url(ws_url):
                errors[const.CONF_WS_URL] = "invalid_ws_url"
            if not errors:
                details = await self._send_sms(phone, errors)
                if not errors:
                    return await self.async_step_code()

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(const.CONF_PHONE, default=self._phone or ""): str,
                    vol.Optional(const.CONF_API_BASE, default=self._api_base): str,
                    vol.Optional(const.CONF_WS_URL, default=self._ws_url): str,
                }
            ),
            errors=errors,
            description_placeholders={"details": details},
        )

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Verify SMS code and fetch available device groups."""
        errors: dict[str, str] = {}
        if user_input is not None and (result := await self._login_with_code(str(user_input[const.CONF_CODE]), errors)):
            self._token, self._user_id = result["token"], result["user_id"]
            self._devices = [d for d in result["devices"] if _device_key(d)]
            if not self._devices:
                errors["base"] = "no_devices"
            elif len(self._devices) == 1:
                return await self._async_create_entry(self._devices[0])
            else:
                return await self.async_step_device()
        return self.async_show_form(
            step_id="code", data_schema=vol.Schema({vol.Required(const.CONF_CODE): str}), errors=errors
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Select a Netlea device group."""
        errors: dict[str, str] = {}
        options = {key: _device_label(d) for d in self._devices if (key := _device_key(d))}
        if user_input is not None:
            selected = str(user_input["device"])
            for device in self._devices:
                if _device_key(device) == selected:
                    return await self._async_create_entry(device)
            errors["base"] = "device_not_found"
        return self.async_show_form(
            step_id="device", data_schema=vol.Schema({vol.Required("device"): vol.In(options)}), errors=errors
        )

    async def _async_create_entry(self, device: dict[str, Any]) -> config_entries.FlowResult:
        equipment_user_id = _device_key(device)
        if not equipment_user_id:
            return self.async_abort(reason="device_not_found")
        await self.async_set_unique_id(equipment_user_id)
        self._abort_if_unique_id_configured()
        device_name = str(device.get("name") or "尼特利鱼缸")
        return self.async_create_entry(
            title=device_name,
            data={
                const.CONF_TRANSPORT_MODE: const.TRANSPORT_CLOUD_WS,
                const.CONF_MODEL: str(device.get("name") or const.DEFAULT_MODEL),
                const.CONF_PHONE: self._phone,
                const.CONF_UUID: self._uuid,
                const.CONF_TOKEN: self._token,
                const.CONF_USER_ID: self._user_id,
                const.CONF_EQUIPMENT_USER_ID: equipment_user_id,
                const.CONF_DEVICE_NAME: device_name,
                const.CONF_CONTROL_REAL_ADDRESS: str(
                    device.get("controlRealAddress") or device.get("realAddress") or ""
                ),
                const.CONF_API_BASE: self._api_base,
                const.CONF_WS_URL: self._ws_url,
            },
        )

    def _resolve_reauth_entry(self) -> config_entries.ConfigEntry | None:
        if hasattr(self, "_get_reauth_entry"):
            try:
                return self._get_reauth_entry()
            except Exception:  # noqa: BLE001
                pass
        entry_id = self.context.get("entry_id")
        return self.hass.config_entries.async_get_entry(entry_id) if entry_id and self.hass else None

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> config_entries.FlowResult:
        """Start reauthentication for an existing config entry."""
        self._reauth_entry = self._resolve_reauth_entry()
        self._phone = str(entry_data.get(const.CONF_PHONE) or "")
        api_base = str(entry_data.get(const.CONF_API_BASE) or const.API_BASE).strip()
        raw_ws = entry_data.get(const.CONF_WS_URL)
        ws_url = normalize_legacy_ws_url(str(raw_ws) if raw_ws is not None else None)
        self._api_base = api_base if is_valid_api_base(api_base) else const.API_BASE
        self._ws_url = ws_url if is_valid_ws_url(ws_url) else const.WS_URL
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Confirm phone number and send a new SMS code during reauth."""
        errors: dict[str, str] = {}
        details = ""
        if user_input is not None:
            phone = _normalize_phone(user_input.get(const.CONF_PHONE))
            self._phone = phone
            if not (len(phone) == 11 and phone.startswith("1")):
                errors[const.CONF_PHONE] = "invalid_phone"
            else:
                details = await self._send_sms(phone, errors)
                if not errors:
                    return await self.async_step_reauth_code()
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(const.CONF_PHONE, default=self._phone or ""): str}),
            errors=errors,
            description_placeholders={"details": details},
        )

    async def async_step_reauth_code(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Verify reauth SMS code and update stored credentials."""
        errors: dict[str, str] = {}
        reauth_entry = self._reauth_entry or self._resolve_reauth_entry()
        if user_input is not None:
            if reauth_entry is None:
                return self.async_abort(reason="device_not_found")
            if result := await self._login_with_code(str(user_input[const.CONF_CODE]), errors):
                matched = next(
                    (
                        d
                        for d in result["devices"]
                        if isinstance(d, dict) and _device_matches_entry(reauth_entry.data, d)
                    ),
                    None,
                )
                if matched is None:
                    errors["base"] = "device_not_found"
                else:
                    data = {
                        **reauth_entry.data,
                        const.CONF_PHONE: self._phone,
                        const.CONF_UUID: self._uuid,
                        const.CONF_TOKEN: result["token"],
                        const.CONF_USER_ID: result["user_id"],
                        const.CONF_API_BASE: self._api_base,
                        const.CONF_WS_URL: self._ws_url,
                        const.CONF_DEVICE_NAME: str(
                            matched.get("name") or reauth_entry.data.get(const.CONF_DEVICE_NAME) or "尼特利鱼缸"
                        ),
                        const.CONF_CONTROL_REAL_ADDRESS: str(
                            matched.get("controlRealAddress")
                            or matched.get("realAddress")
                            or reauth_entry.data.get(const.CONF_CONTROL_REAL_ADDRESS)
                            or ""
                        ),
                    }
                    if hasattr(self, "async_update_reload_and_abort"):
                        return self.async_update_reload_and_abort(reauth_entry, data=data)
                    self.hass.config_entries.async_update_entry(reauth_entry, data=data)
                    await self.hass.config_entries.async_reload(reauth_entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_code", data_schema=vol.Schema({vol.Required(const.CONF_CODE): str}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlowWithReload:
        return NetleaOptionsFlow()


class NetleaOptionsFlow(config_entries.OptionsFlowWithReload):
    """Handle options flow for Netlea Aquarium (including transport switching without losing entity IDs)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Manage transport backend, model, control semantics, polling, and temporary viewing light channels."""
        errors: dict[str, str] = {}
        entry = self.config_entry
        options = dict(entry.options)
        data = dict(entry.data)

        if user_input is not None:
            mode = str(
                user_input.get(const.CONF_TRANSPORT_MODE)
                or options.get(const.CONF_TRANSPORT_MODE)
                or data.get(const.CONF_TRANSPORT_MODE)
                or const.TRANSPORT_CLOUD_WS
            )
            aes_key = str(
                user_input.get(const.CONF_MQTT_AES_KEY)
                if const.CONF_MQTT_AES_KEY in user_input
                else (options.get(const.CONF_MQTT_AES_KEY) or data.get(const.CONF_MQTT_AES_KEY) or "")
            ).strip()
            if mode == const.TRANSPORT_LOCAL_MQTT:
                try:
                    parse_aes_key(aes_key)
                except NetleaError:
                    errors[const.CONF_MQTT_AES_KEY] = "invalid_aes_key"

            if not errors:
                out: dict[str, Any] = {
                    k: proto.clamp_int(user_input.get(k), d, lo, hi) for k, d, lo, hi in _OPTION_SPECS
                }
                if const.CONF_TRANSPORT_MODE in user_input:
                    out[const.CONF_TRANSPORT_MODE] = mode
                elif const.CONF_TRANSPORT_MODE in options:
                    out[const.CONF_TRANSPORT_MODE] = options[const.CONF_TRANSPORT_MODE]
                if const.CONF_MODEL in user_input:
                    out[const.CONF_MODEL] = str(user_input[const.CONF_MODEL])
                elif const.CONF_MODEL in options:
                    out[const.CONF_MODEL] = options[const.CONF_MODEL]
                if const.CONF_CONTROL_MODE in user_input:
                    cm = str(user_input[const.CONF_CONTROL_MODE])
                    out[const.CONF_CONTROL_MODE] = (
                        cm if cm in const.VALID_CONTROL_MODES else const.CONTROL_MODE_SCHEDULE_AUTO
                    )
                if const.CONF_MQTT_PORT in user_input:
                    out[const.CONF_MQTT_PORT] = proto.clamp_int(
                        user_input[const.CONF_MQTT_PORT],
                        const.DEFAULT_MQTT_PORT,
                        const.MIN_MQTT_PORT,
                        const.MAX_MQTT_PORT,
                    )
                if aes_key:
                    out[const.CONF_MQTT_AES_KEY] = aes_key.upper()
                if const.CONF_CONTROL_REAL_ADDRESS in user_input and str(user_input[const.CONF_CONTROL_REAL_ADDRESS]).strip():
                    out[const.CONF_CONTROL_REAL_ADDRESS] = ",".join(
                        proto.split_addresses(user_input[const.CONF_CONTROL_REAL_ADDRESS])
                    )
                return self.async_create_entry(title="", data=out)

        cur_mode = str(options.get(const.CONF_TRANSPORT_MODE) or data.get(const.CONF_TRANSPORT_MODE) or const.TRANSPORT_CLOUD_WS)
        cur_model = str(options.get(const.CONF_MODEL) or data.get(const.CONF_MODEL) or const.DEFAULT_MODEL)
        cur_ctrl = str(options.get(const.CONF_CONTROL_MODE) or const.CONTROL_MODE_SCHEDULE_AUTO)
        cur_addrs = str(options.get(const.CONF_CONTROL_REAL_ADDRESS) or data.get(const.CONF_CONTROL_REAL_ADDRESS) or "")
        cur_port = proto.clamp_int(
            options.get(const.CONF_MQTT_PORT) or data.get(const.CONF_MQTT_PORT),
            const.DEFAULT_MQTT_PORT,
            const.MIN_MQTT_PORT,
            const.MAX_MQTT_PORT,
        )
        cur_aes = str(options.get(const.CONF_MQTT_AES_KEY) or data.get(const.CONF_MQTT_AES_KEY) or "")

        schema_dict: dict[Any, Any] = {
            vol.Required(const.CONF_TRANSPORT_MODE, default=cur_mode): vol.In(const.VALID_TRANSPORTS),
            vol.Required(const.CONF_MODEL, default=cur_model if cur_model in const.SUPPORTED_MODELS else const.DEFAULT_MODEL): vol.In(const.SUPPORTED_MODELS),
            vol.Required(const.CONF_CONTROL_MODE, default=cur_ctrl): vol.In(const.VALID_CONTROL_MODES),
            vol.Optional(const.CONF_CONTROL_REAL_ADDRESS, default=cur_addrs): str,
            vol.Optional(const.CONF_MQTT_PORT, default=cur_port): vol.All(
                vol.Coerce(int), vol.Range(min=const.MIN_MQTT_PORT, max=const.MAX_MQTT_PORT)
            ),
            vol.Optional(const.CONF_MQTT_AES_KEY, default=cur_aes): str,
        }
        for k, d, lo, hi in _OPTION_SPECS:
            schema_dict[vol.Required(k, default=proto.clamp_int(options.get(k), d, lo, hi))] = vol.All(
                vol.Coerce(int), vol.Range(min=lo, max=hi)
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict), errors=errors)
