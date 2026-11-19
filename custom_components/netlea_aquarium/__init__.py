"""Netlea Aquarium integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import NetleaClient, normalize_legacy_ws_url
from .const import API_BASE, CONF_API_BASE, CONF_WS_URL
from .coordinator import NetleaCoordinator

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.BUTTON, Platform.SENSOR, Platform.BINARY_SENSOR]
type NetleaConfigEntry = ConfigEntry[NetleaCoordinator]


def _migrate_entry_data(entry_data: dict[str, object]) -> dict[str, object]:
    """Migrate legacy config entry defaults to TLS endpoints."""
    migrated = dict(entry_data)
    if not migrated.get(CONF_API_BASE):
        migrated[CONF_API_BASE] = API_BASE
    raw_ws = migrated.get(CONF_WS_URL)
    migrated[CONF_WS_URL] = normalize_legacy_ws_url(str(raw_ws) if raw_ws is not None else None)
    return migrated


async def async_setup_entry(hass: HomeAssistant, entry: NetleaConfigEntry) -> bool:
    """Set up Netlea Aquarium from a config entry."""
    migrated = _migrate_entry_data(dict(entry.data))
    if migrated != dict(entry.data):
        hass.config_entries.async_update_entry(entry, data=migrated)
    client = NetleaClient.from_config_entry_data(async_get_clientsession(hass), migrated)
    coordinator = NetleaCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: NetleaConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
