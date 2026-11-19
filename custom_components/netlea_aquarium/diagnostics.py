"""Diagnostics support for Netlea Aquarium."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import NetleaConfigEntry, const

TO_REDACT = {
    const.CONF_TOKEN, const.CONF_PHONE, const.CONF_CODE, const.CONF_UUID,
    const.CONF_USER_ID, const.CONF_EQUIPMENT_USER_ID, const.CONF_CONTROL_REAL_ADDRESS,
    "authorization", "Authorization", "phone", "phonenumber", "mobile", "username", "password",
    "userId", "equipmentUserId", "realAddress", "controlRealAddress",
    "firstConnectRealAddress", "onLineRealAddress",
    "expected_addresses", "replied_addresses", "missing_addresses",
}


def _last_control_summary(last_control: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(last_control, dict):
        return None
    return {
        **last_control,
        "expected_address_count": len(last_control.get("expected_addresses") or ()),
        "replied_address_count": len(last_control.get("replied_addresses") or ()),
        "missing_address_count": len(last_control.get("missing_addresses") or ()),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NetleaConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a Netlea config entry."""
    coordinator = entry.runtime_data
    payload = {
        "entry": {"title": entry.title, "data": dict(entry.data), "options": dict(entry.options)},
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds() if coordinator.update_interval else None
            ),
            "stale_failures_limit": coordinator.stale_failures,
            "connection_failures": coordinator.connection_failures,
            "temp_channels": dict(coordinator.temp_channels),
            "last_control": _last_control_summary(coordinator.last_control),
            "data": coordinator.data,
        },
    }
    return async_redact_data(payload, TO_REDACT)
