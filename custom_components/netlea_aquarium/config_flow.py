"""Config flow for Netlea Aquarium."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NetleaAuthError, NetleaClient, NetleaError, as_int
from .const import (
    API_BASE,
    CONF_API_BASE,
    CONF_CODE,
    CONF_CONTROL_REAL_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_EQUIPMENT_USER_ID,
    CONF_PHONE,
    CONF_TOKEN,
    CONF_USER_ID,
    CONF_UUID,
    CONF_WS_URL,
    DOMAIN,
    WS_URL,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_phone(value: Any) -> str:
    """Normalize a mainland China phone number."""
    digits = "".join(char for char in str(value or "") if char.isdigit())
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits


def _is_valid_phone(value: str) -> bool:
    """Return whether the phone number looks valid."""
    return len(value) == 11 and value.startswith("1")


def _error_details(err: Exception) -> str:
    """Build one concise error string for the form description."""
    text = " ".join(str(err).split()).strip()
    if not text:
        return ""
    return f"\n\n错误详情：{text[:160]}"


def _clean_value(value: Any) -> str | None:
    """Return a non-empty cloud value."""
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return None
    return text


def _device_key(device: dict[str, Any]) -> str | None:
    """Return the stable key for one cloud device."""
    return (
        _clean_value(device.get("equipmentUserId"))
        or _clean_value(device.get("controlRealAddress"))
        or _clean_value(device.get("realAddress"))
    )


def _device_label(device: dict[str, Any]) -> str:
    name = str(device.get("name") or "Netlea")
    address = str(device.get("controlRealAddress") or device.get("realAddress") or "")
    shared = "共享" if as_int(device.get("shareFlag"), 0) == 2 else "自有"
    return f"{name} | {shared} | {address}"


def _device_matches_entry(entry_data: dict[str, Any], device: dict[str, Any]) -> bool:
    """Return whether a cloud device matches an existing config entry."""
    entry_key = _clean_value(entry_data.get(CONF_EQUIPMENT_USER_ID))
    entry_address = _clean_value(entry_data.get(CONF_CONTROL_REAL_ADDRESS))
    device_key = _device_key(device)
    device_addresses = {
        value
        for value in (
            _clean_value(device.get("controlRealAddress")),
            _clean_value(device.get("realAddress")),
        )
        if value
    }
    return bool(
        (entry_key and entry_key == device_key)
        or (entry_key and entry_key in device_addresses)
        or (entry_address and entry_address == device_key)
        or (entry_address and entry_address in device_addresses)
    )


class NetleaAquariumConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Netlea Aquarium."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._phone: str | None = None
        self._uuid: str | None = None
        self._token: str | None = None
        self._user_id: str | None = None
        self._devices: list[dict[str, Any]] = []
        self._reauth_entry: config_entries.ConfigEntry | None = None

    def _client(self) -> NetleaClient:
        return NetleaClient(
            async_get_clientsession(self.hass),
            api_base=API_BASE,
            ws_url=WS_URL,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Start setup by sending an SMS code."""
        errors: dict[str, str] = {}
        details = ""
        if user_input is not None:
            phone = _normalize_phone(user_input[CONF_PHONE])
            self._phone = phone
            if not _is_valid_phone(phone):
                errors["base"] = "invalid_phone"
            else:
                try:
                    self._uuid = await self._client().async_send_code(phone)
                    return await self.async_step_code()
                except NetleaError as err:
                    _LOGGER.exception("Failed to send Netlea SMS code")
                    errors["base"] = "send_code_failed"
                    details = _error_details(err)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_PHONE, default=self._phone or ""): str}),
            errors=errors,
            description_placeholders={"details": details},
        )

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Login with the SMS code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                result = await self._client().async_login(
                    str(self._phone),
                    str(user_input[CONF_CODE]).strip(),
                    str(self._uuid),
                )
                self._token = result["token"]
                self._user_id = result["user_id"]
                self._devices = [device for device in result["devices"] if _device_key(device)]
                if not self._devices:
                    errors["base"] = "no_devices"
                elif len(self._devices) == 1:
                    return await self._async_create_entry(self._devices[0])
                else:
                    return await self.async_step_device()
            except NetleaAuthError:
                errors["base"] = "invalid_auth"
            except NetleaError:
                _LOGGER.exception("Failed to login to Netlea")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
        )

    async def async_step_device(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Select a device group."""
        errors: dict[str, str] = {}
        options = {key: _device_label(device) for device in self._devices if (key := _device_key(device))}
        if user_input is not None:
            selected = str(user_input["device"])
            for device in self._devices:
                if _device_key(device) == selected:
                    return await self._async_create_entry(device)
            errors["base"] = "device_not_found"

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(options)}),
            errors=errors,
        )

    async def _async_create_entry(self, device: dict[str, Any]) -> config_entries.FlowResult:
        """Create the config entry."""
        equipment_user_id = _device_key(device)
        if not equipment_user_id:
            return self.async_abort(reason="device_not_found")
        await self.async_set_unique_id(equipment_user_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title=str(device.get("name") or "尼特利鱼缸"),
            data={
                CONF_PHONE: self._phone,
                CONF_UUID: self._uuid,
                CONF_TOKEN: self._token,
                CONF_USER_ID: self._user_id,
                CONF_EQUIPMENT_USER_ID: equipment_user_id,
                CONF_DEVICE_NAME: str(device.get("name") or "尼特利鱼缸"),
                CONF_CONTROL_REAL_ADDRESS: str(device.get("controlRealAddress") or device.get("realAddress") or ""),
                CONF_API_BASE: API_BASE,
                CONF_WS_URL: WS_URL,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> config_entries.FlowResult:
        """Handle reauthentication."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._phone = str(entry_data.get(CONF_PHONE) or "")
        return await self.async_step_reauth_user()

    async def async_step_reauth_user(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Send a new SMS code during reauth."""
        errors: dict[str, str] = {}
        details = ""
        if user_input is not None:
            phone = _normalize_phone(user_input[CONF_PHONE])
            self._phone = phone
            if not _is_valid_phone(phone):
                errors["base"] = "invalid_phone"
            else:
                try:
                    self._uuid = await self._client().async_send_code(phone)
                    return await self.async_step_reauth_code()
                except NetleaError as err:
                    _LOGGER.exception("Failed to send Netlea reauth SMS code")
                    errors["base"] = "send_code_failed"
                    details = _error_details(err)

        return self.async_show_form(
            step_id="reauth_user",
            data_schema=vol.Schema({vol.Required(CONF_PHONE, default=self._phone or ""): str}),
            errors=errors,
            description_placeholders={"details": details},
        )

    async def async_step_reauth_code(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Complete reauth with SMS code."""
        errors: dict[str, str] = {}
        if user_input is not None and self._reauth_entry is not None:
            try:
                result = await self._client().async_login(
                    str(self._phone),
                    str(user_input[CONF_CODE]).strip(),
                    str(self._uuid),
                )
                matching_device = next(
                    (
                        device
                        for device in result["devices"]
                        if isinstance(device, dict) and _device_matches_entry(self._reauth_entry.data, device)
                    ),
                    None,
                )
                if matching_device is None:
                    errors["base"] = "device_not_found"
                    return self.async_show_form(
                        step_id="reauth_code",
                        data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
                        errors=errors,
                    )
                data = {
                    **self._reauth_entry.data,
                    CONF_PHONE: self._phone,
                    CONF_UUID: self._uuid,
                    CONF_TOKEN: result["token"],
                    CONF_USER_ID: result["user_id"],
                    CONF_DEVICE_NAME: str(
                        matching_device.get("name")
                        or self._reauth_entry.data.get(CONF_DEVICE_NAME)
                        or "尼特利鱼缸"
                    ),
                    CONF_CONTROL_REAL_ADDRESS: str(
                        matching_device.get("controlRealAddress")
                        or matching_device.get("realAddress")
                        or self._reauth_entry.data.get(CONF_CONTROL_REAL_ADDRESS)
                        or ""
                    ),
                }
                self.hass.config_entries.async_update_entry(self._reauth_entry, data=data)
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
            except NetleaAuthError:
                errors["base"] = "invalid_auth"
            except NetleaError:
                _LOGGER.exception("Failed to reauth Netlea")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_code",
            data_schema=vol.Schema({vol.Required(CONF_CODE): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        """Return options flow."""
        return NetleaAquariumOptionsFlow()


class NetleaAquariumOptionsFlow(config_entries.OptionsFlow):
    """No-op options flow placeholder."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> config_entries.FlowResult:
        """Show options."""
        return self.async_create_entry(title="", data={})
