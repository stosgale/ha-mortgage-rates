"""Sensor platform for ha_mortgage_rates.

This platform creates a set of sensors for every (rate_type, term) group
returned by the coordinator.  Each group gets three sensors:

- ``{group_key}_rate``            – the cheapest initial rate (percentage)
- ``{group_key}_lender``          – the lender offering that product
- ``{group_key}_monthly_payment`` – the initial monthly payment (GBP)

Sensor entity names follow the pattern "<Type> <Term>yr <Field>", e.g.
"Fixed 2yr Rate" or "Variable Unknown Term Lender".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class _FieldMeta:
    """Metadata describing one of the per-group sensor fields."""

    field: str
    unit: str | None
    state_class: SensorStateClass | None
    icon: str
    precision: int | None = None


# Field definitions for each (rate_type, term) group.
_FIELD_METAS: tuple[_FieldMeta, ...] = (
    _FieldMeta(
        field="rate",
        unit="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent",
        precision=2,
    ),
    _FieldMeta(
        field="lender",
        unit=None,
        state_class=None,
        icon="mdi:bank",
        precision=None,
    ),
    _FieldMeta(
        field="monthly_payment",
        unit="GBP",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash",
        precision=2,
    ),
)


def _display_name(group_key: str, field: str) -> str:
    """Return a human-readable entity name suffix for a group/field pair.

    Examples:
      - ``fixed_2yr`` + ``rate``            -> ``Fixed 2yr Rate``
      - ``fixed_2yr__nationwide`` + ``rate`` -> ``Fixed 2yr Nationwide Rate``
      - ``variable_unknown_term`` + ``lender`` -> ``Variable Unknown Term Lender``
    """
    if "__" in group_key:
        base, lender = group_key.rsplit("__", 1)
        base_name = _display_name(base, field)
        lender_display = " ".join(part.capitalize() for part in lender.split("_"))
        parts = base_name.rsplit(" ", 1)
        return f"{parts[0]} {lender_display} {parts[1]}"

    parts = group_key.split("_")
    term_token = parts[-1].replace("-", " ").replace("_", " ")
    type_parts = parts[:-1]
    type_name = " ".join(part.capitalize() for part in type_parts) if type_parts else "Unknown"
    field_name = " ".join(part.capitalize() for part in field.split("_"))
    term_token = " ".join(part.capitalize() for part in term_token.split())
    return f"{type_name} {term_token} {field_name}"


class MortgageRateSensor(CoordinatorEntity, SensorEntity):
    """Representation of one field of a mortgage rate group."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[dict[str, Any]],
        entry: ConfigEntry,
        group_key: str,
        meta: _FieldMeta,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._group_key = group_key
        self._meta = meta
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{group_key}_{meta.field}"
        self._attr_name = _display_name(group_key, meta.field)
        self._attr_native_unit_of_measurement = meta.unit
        self._attr_state_class = meta.state_class
        self._attr_icon = meta.icon
        if meta.precision is not None:
            self._attr_suggested_display_precision = meta.precision

    @property
    def native_value(self) -> Any | None:
        """Return the native value of the sensor."""
        data = self.coordinator.data
        if not data:
            return None
        group = data.get(self._group_key)
        if not isinstance(group, dict):
            return None
        return group.get(self._meta.field)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the group's full product data plus the top-level timestamp."""
        data = self.coordinator.data or {}
        group = data.get(self._group_key)
        attrs: dict[str, Any] = {}
        if isinstance(group, dict):
            attrs.update(group)
        attrs["last_updated"] = data.get("last_updated")
        attrs["_config_mortgage_amount"] = self._entry.data.get("mortgage_amount")
        attrs["_config_term"] = self._entry.data.get("term")
        return attrs


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for group_key in coordinator.data:
        if group_key == "last_updated":
            continue
        for meta in _FIELD_METAS:
            entities.append(MortgageRateSensor(coordinator, entry, group_key, meta))

    async_add_entities(entities)
