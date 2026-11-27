"""Netlea Aquarium integration (v2.0.0 — Local MQTT / Local BLE / Cloud WS)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import const, protocol as proto
from .client import NetleaClient, normalize_legacy_ws_url
from .coordinator import NetleaCoordinator
from .transport import CloudWsTransport, LocalBleTransport, LocalMqttServerTransport, NetleaTransport

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.LIGHT,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.FAN,
    Platform.NUMBER,
    Platform.SWITCH,
]
type NetleaConfigEntry = ConfigEntry[NetleaCoordinator]


def _migrate_entry_data(entry_data: dict[str, object]) -> dict[str, object]:
    """Migrate legacy config entry defaults to TLS endpoints and v2 transport metadata."""
    migrated = dict(entry_data)
    if not migrated.get(const.CONF_TRANSPORT_MODE):
        migrated[const.CONF_TRANSPORT_MODE] = const.TRANSPORT_CLOUD_WS
    if not migrated.get(const.CONF_MODEL):
        migrated[const.CONF_MODEL] = const.DEFAULT_MODEL
    if migrated.get(const.CONF_TRANSPORT_MODE) == const.TRANSPORT_CLOUD_WS:
        if not migrated.get(const.CONF_API_BASE):
            migrated[const.CONF_API_BASE] = const.API_BASE
        raw_ws = migrated.get(const.CONF_WS_URL)
        migrated[const.CONF_WS_URL] = normalize_legacy_ws_url(str(raw_ws) if raw_ws is not None else None)
    return migrated


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate v1 config entries smoothly to v2 preserving unique_ids and credentials."""
    if entry.version < 2:
        migrated = _migrate_entry_data(dict(entry.data))
        hass.config_entries.async_update_entry(entry, data=migrated, version=2)
        _LOGGER.info("Migrated Netlea Aquarium config entry %s from v%s to v2", entry.entry_id, entry.version)
    return True


def _build_transport(
    hass: HomeAssistant, entry_data: dict[str, Any], options: dict[str, Any]
) -> tuple[NetleaTransport, NetleaClient | None]:
    """Instantiate the configured transport backend."""
    mode = str(options.get(const.CONF_TRANSPORT_MODE) or entry_data.get(const.CONF_TRANSPORT_MODE) or const.TRANSPORT_CLOUD_WS)
    addrs = proto.split_addresses(
        options.get(const.CONF_CONTROL_REAL_ADDRESS) or entry_data.get(const.CONF_CONTROL_REAL_ADDRESS) or ""
    )
    if mode == const.TRANSPORT_LOCAL_MQTT:
        aes_key = str(options.get(const.CONF_MQTT_AES_KEY) or entry_data.get(const.CONF_MQTT_AES_KEY) or "").strip()
        port = proto.clamp_int(
            options.get(const.CONF_MQTT_PORT) or entry_data.get(const.CONF_MQTT_PORT),
            const.DEFAULT_MQTT_PORT,
            const.MIN_MQTT_PORT,
            const.MAX_MQTT_PORT,
        )
        bind_host = str(
            options.get(const.CONF_MQTT_BIND_HOST) or entry_data.get(const.CONF_MQTT_BIND_HOST) or const.DEFAULT_MQTT_BIND_HOST
        )
        transport = LocalMqttServerTransport(
            allowed_addresses=addrs,
            aes_key_hex=aes_key,
            bind_host=bind_host,
            port=port,
            now_fn=dt_util.now,
        )
        return transport, None

    if mode == const.TRANSPORT_LOCAL_BLE:
        return LocalBleTransport(hass, allowed_addresses=addrs), None

    client = NetleaClient.from_config_entry_data(async_get_clientsession(hass), entry_data)
    return CloudWsTransport(client), client


def _resolve_coordinator(hass: HomeAssistant, entry_id: str | None) -> NetleaCoordinator:
    entries = list(hass.config_entries.async_entries(const.DOMAIN))
    if entry_id:
        for entry in entries:
            if entry.entry_id == entry_id and getattr(entry, "runtime_data", None):
                return entry.runtime_data
        raise HomeAssistantError(f"未找到指定的尼特利配置项：{entry_id}")
    for entry in entries:
        if getattr(entry, "runtime_data", None):
            return entry.runtime_data
    raise HomeAssistantError("当前没有已加载的尼特利鱼缸灯集成实例")


def _async_register_services(hass: HomeAssistant) -> None:
    """Register Netlea domain services once."""
    services = getattr(hass, "services", None)
    if services is None or not hasattr(services, "async_register"):
        return
    if services.has_service(const.DOMAIN, const.SERVICE_SET_CHANNELS):
        return

    async def _handle_set_channels(call: ServiceCall) -> None:
        coord = _resolve_coordinator(hass, call.data.get("entry_id"))
        await coord.async_set_main_channels(
            int(call.data["red"]),
            int(call.data["white"]),
            int(call.data["green"]),
            int(call.data["blue"]),
            call.data.get("fan"),
            mode_override=call.data.get("mode"),
            duration_minutes=call.data.get("duration_minutes"),
        )

    async def _handle_apply_preset(call: ServiceCall) -> None:
        coord = _resolve_coordinator(hass, call.data.get("entry_id"))
        await coord.async_apply_preset(
            str(call.data["preset"]),
            grade=call.data.get("grade"),
            mode_override=call.data.get("mode"),
            duration_minutes=call.data.get("duration_minutes"),
        )

    async def _handle_query_state(call: ServiceCall) -> None:
        coord = _resolve_coordinator(hass, call.data.get("entry_id"))
        await coord.async_refresh_state(include_schedule=bool(call.data.get("include_schedule", True)))

    async def _handle_set_schedule(call: ServiceCall) -> None:
        coord = _resolve_coordinator(hass, call.data.get("entry_id"))
        await coord.async_set_schedule(
            list(call.data["trips"]),
            sunrise_minutes=int(call.data.get("sunrise_minutes", 0)),
            sunset_minutes=int(call.data.get("sunset_minutes", 0)),
            resume=bool(call.data.get("resume", True)),
        )

    services.async_register(
        const.DOMAIN,
        const.SERVICE_SET_CHANNELS,
        _handle_set_channels,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("red"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                vol.Required("white"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                vol.Required("green"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                vol.Required("blue"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                vol.Optional("fan"): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                vol.Optional("mode"): vol.In(const.VALID_CONTROL_MODES),
                vol.Optional("duration_minutes"): vol.All(
                    vol.Coerce(int), vol.Range(min=const.MIN_TEMP_MINUTES, max=const.MAX_TEMP_MINUTES)
                ),
            }
        ),
    )
    services.async_register(
        const.DOMAIN,
        const.SERVICE_APPLY_PRESET,
        _handle_apply_preset,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("preset"): str,
                vol.Optional("grade"): vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
                vol.Optional("mode"): vol.In(const.VALID_CONTROL_MODES),
                vol.Optional("duration_minutes"): vol.All(
                    vol.Coerce(int), vol.Range(min=const.MIN_TEMP_MINUTES, max=const.MAX_TEMP_MINUTES)
                ),
            }
        ),
    )
    services.async_register(
        const.DOMAIN,
        const.SERVICE_QUERY_STATE,
        _handle_query_state,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("include_schedule", default=True): bool,
            }
        ),
    )
    services.async_register(
        const.DOMAIN,
        const.SERVICE_SET_SCHEDULE,
        _handle_set_schedule,
        schema=vol.Schema(
            {
                vol.Optional("entry_id"): str,
                vol.Required("trips"): list,
                vol.Optional("sunrise_minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
                vol.Optional("sunset_minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
                vol.Optional("resume", default=True): bool,
            }
        ),
    )


async def async_setup_entry(hass: HomeAssistant, entry: NetleaConfigEntry) -> bool:
    """Set up Netlea Aquarium from a config entry."""
    migrated = _migrate_entry_data(dict(entry.data))
    if migrated != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=migrated)
    options = dict(getattr(entry, "options", None) or {})
    transport, client = _build_transport(hass, migrated, options)
    await transport.async_start()
    coordinator = NetleaCoordinator(hass, transport, entry, client=client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await transport.async_stop()
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NetleaConfigEntry) -> bool:
    """Unload a config entry and cleanly close local transport listeners."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is not None and getattr(coordinator, "transport", None) is not None:
        await coordinator.transport.async_stop()
    return unload_ok
