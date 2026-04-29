"""Constants for the Netlea Aquarium integration."""

from __future__ import annotations

DOMAIN = "netlea_aquarium"
CONF_PHONE = "phone"

API_BASE = "https://v3api.netlea.com/prod-api"
WS_URL = "ws://v3ws.netlea.com/websocket"

CONF_API_BASE = "api_base"
CONF_CODE = "code"
CONF_CONTROL_REAL_ADDRESS = "control_real_address"
CONF_DEVICE_NAME = "device_name"
CONF_EQUIPMENT_USER_ID = "equipment_user_id"
CONF_TOKEN = "token"
CONF_USER_ID = "user_id"
CONF_UUID = "uuid"
CONF_WS_URL = "ws_url"

ACTION_TEMP_ON = "temp_on"
ACTION_TEMP_OFF = "temp_off"
ACTION_RESUME_SCHEDULE = "resume_schedule"

DEFAULT_SCAN_INTERVAL = 60
DEFAULT_REQUEST_RETRIES = 3
DEFAULT_REQUEST_TIMEOUT = 12
DEFAULT_STALE_FAILURES = 2

__all__ = [
    "ACTION_RESUME_SCHEDULE",
    "ACTION_TEMP_OFF",
    "ACTION_TEMP_ON",
    "API_BASE",
    "CONF_API_BASE",
    "CONF_CODE",
    "CONF_CONTROL_REAL_ADDRESS",
    "CONF_DEVICE_NAME",
    "CONF_EQUIPMENT_USER_ID",
    "CONF_PHONE",
    "CONF_TOKEN",
    "CONF_USER_ID",
    "CONF_UUID",
    "CONF_WS_URL",
    "DEFAULT_REQUEST_RETRIES",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_SCAN_INTERVAL",
    "DEFAULT_STALE_FAILURES",
    "DOMAIN",
    "WS_URL",
]
