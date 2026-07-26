"""Sensor platform for ha_mortgage_rates."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MortgageRateSensorEntityDescription(SensorEntityDescription):
    """Describes a mortgage rate sensor."""

    value_fn: Callable[[dict[str, Any]], Any | None] = lambda data: None


SENSOR_DESCRIPTIONS: list[MortgageRateSensorEntityDescription] = [
    MortgageRateSensorEntityDescription(
        key="best_rate",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=None,
        suggested_display_precision=2,
        value_fn=lambda data: data.get("best_rate"),
    ),
]


class MortgageRateSensor(CoordinatorEntity, SensorEntity):
    """Representation of a mortgage rate sensor."""

    entity_description: MortgageRateSensorEntityDescription
    _attr_icon = "mdi:percent"

    def __init__(
        self,
        coordinator,
        description: MortgageRateSensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_has_entity_name = True
        self._attr_unique_id = f"{entry.entry_id}_best_rate"

    @property
    def native_value(self) -> Any | None:
        """Return the native value of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        data = self.coordinator.data or {}
        return {
            "lender": data.get("lender"),
            "aprc": data.get("aprc"),
            "product_fees": data.get("product_fees"),
            "rate_type": data.get("rate_type"),
            "initial_term_years": data.get("initial_term_years"),
            "max_ltv": data.get("max_ltv"),
            "monthly_payment": data.get("monthly_payment"),
            "last_updated": data.get("last_updated"),
        }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [MortgageRateSensor(coordinator, SENSOR_DESCRIPTIONS[0], entry)]
    )
