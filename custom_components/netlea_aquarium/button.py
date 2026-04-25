"""Button platform for Netlea Aquarium."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ACTION_RESUME_SCHEDULE, ACTION_TEMP_OFF, ACTION_TEMP_ON, DOMAIN
from .coordinator import NetleaDataUpdateCoordinator
from .entity import NetleaBaseEntity


@dataclass(frozen=True, kw_only=True)
class NetleaButtonEntityDescription(ButtonEntityDescription):
    """Netlea button description."""

    action: str


BUTTONS: tuple[NetleaButtonEntityDescription, ...] = (
    NetleaButtonEntityDescription(
        key=ACTION_TEMP_ON,
        translation_key="temp_on",
        icon="mdi:lightbulb-on",
        action=ACTION_TEMP_ON,
    ),
    NetleaButtonEntityDescription(
        key=ACTION_TEMP_OFF,
        translation_key="temp_off",
        icon="mdi:lightbulb-off",
        action=ACTION_TEMP_OFF,
    ),
    NetleaButtonEntityDescription(
        key=ACTION_RESUME_SCHEDULE,
        translation_key="resume_schedule",
        icon="mdi:calendar-sync",
        action=ACTION_RESUME_SCHEDULE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Netlea buttons."""
    coordinator: NetleaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([NetleaActionButton(coordinator, description) for description in BUTTONS])


class NetleaActionButton(NetleaBaseEntity, ButtonEntity):
    """Netlea action button."""

    entity_description: NetleaButtonEntityDescription

    def __init__(
        self,
        coordinator: NetleaDataUpdateCoordinator,
        description: NetleaButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Press the button."""
        if self.entity_description.action == ACTION_TEMP_ON:
            await self.coordinator.async_temp_on()
        elif self.entity_description.action == ACTION_TEMP_OFF:
            await self.coordinator.async_temp_off()
        elif self.entity_description.action == ACTION_RESUME_SCHEDULE:
            await self.coordinator.async_resume_schedule()
