"""Tests for the HA Mortgage Rates sensor platform."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock the entity_platform module before importing sensor.py so that
# AddConfigEntryEntitiesCallback (added in HA 2025.x) is available
# even on older HA installations used for CI/testing.
_entity_platform_mock = MagicMock()
_entity_platform_mock.AddConfigEntryEntitiesCallback = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = _entity_platform_mock

from homeassistant.components.sensor import SensorStateClass  # noqa: E402

from custom_components.ha_mortgage_rates.sensor import (  # noqa: E402
    MortgageRateSensor,
    _FIELD_METAS,
    _display_name,
    async_setup_entry,
)


def _make_sensor(
    coordinator_data: dict | None,
    entry_id: str = "abc123",
    group_key: str = "fixed_2yr",
    field: str = "rate",
) -> MortgageRateSensor:
    """Build a MortgageRateSensor with mocked coordinator and config entry."""
    coordinator = MagicMock()
    coordinator.data = coordinator_data

    entry = MagicMock()
    entry.entry_id = entry_id

    meta = next(m for m in _FIELD_METAS if m.field == field)
    return MortgageRateSensor(coordinator, entry, group_key, meta)


# ---------------------------------------------------------------------------
# Display name helper
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("group_key", "field", "expected"),
    [
        ("fixed_2yr", "rate", "Fixed 2yr Rate"),
        ("fixed_2yr", "lender", "Fixed 2yr Lender"),
        ("fixed_2yr", "monthly_payment", "Fixed 2yr Monthly Payment"),
        ("variable_unknown_term", "rate", "Variable Unknown Term Rate"),
        ("unknown_type_5yr", "lender", "Unknown Type 5yr Lender"),
    ],
)
def test_display_name(group_key: str, field: str, expected: str) -> None:
    """Test that display names are generated from group keys and fields."""
    assert _display_name(group_key, field) == expected


# ---------------------------------------------------------------------------
# Per-field sensor tests
# ---------------------------------------------------------------------------
def test_rate_sensor_state() -> None:
    """Test rate sensor native_value is the group's rate."""
    data = {
        "fixed_2yr": {
            "rate": 4.47,
            "lender": "Nationwide",
            "monthly_payment": 1123.59,
        },
        "last_updated": "2026-07-26T12:00:00Z",
    }
    sensor = _make_sensor(data, group_key="fixed_2yr", field="rate")
    assert sensor.native_value == 4.47


def test_lender_sensor_state() -> None:
    """Test lender sensor native_value is the group's lender."""
    data = {
        "fixed_2yr": {
            "rate": 4.47,
            "lender": "Nationwide",
            "monthly_payment": 1123.59,
        },
        "last_updated": "2026-07-26T12:00:00Z",
    }
    sensor = _make_sensor(data, group_key="fixed_2yr", field="lender")
    assert sensor.native_value == "Nationwide"


def test_monthly_payment_sensor_state() -> None:
    """Test monthly_payment sensor native_value is the group's payment."""
    data = {
        "fixed_2yr": {
            "rate": 4.47,
            "lender": "Nationwide",
            "monthly_payment": 1123.59,
        },
        "last_updated": "2026-07-26T12:00:00Z",
    }
    sensor = _make_sensor(data, group_key="fixed_2yr", field="monthly_payment")
    assert sensor.native_value == 1123.59


# ---------------------------------------------------------------------------
# Sensor metadata
# ---------------------------------------------------------------------------
def test_rate_sensor_metadata() -> None:
    """Test rate sensor has percent unit, MEASUREMENT state class and percent icon."""
    sensor = _make_sensor({}, group_key="fixed_2yr", field="rate")
    assert sensor.native_unit_of_measurement == "%"
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.icon == "mdi:percent"


def test_lender_sensor_metadata() -> None:
    """Test lender sensor has no unit, no state class and bank icon."""
    sensor = _make_sensor({}, group_key="variable_2yr", field="lender")
    assert sensor.native_unit_of_measurement is None
    assert sensor.state_class is None
    assert sensor.icon == "mdi:bank"


def test_monthly_payment_sensor_metadata() -> None:
    """Test monthly payment sensor has GBP unit, TOTAL state class and cash icon."""
    sensor = _make_sensor({}, group_key="fixed_5yr", field="monthly_payment")
    assert sensor.native_unit_of_measurement == "GBP"
    assert sensor.state_class == SensorStateClass.TOTAL
    assert sensor.icon == "mdi:cash"


# ---------------------------------------------------------------------------
# Naming and identity
# ---------------------------------------------------------------------------
def test_sensor_unique_id() -> None:
    """Test unique_id follows entry_id_group_key_field pattern."""
    sensor = _make_sensor(
        {}, entry_id="abc123", group_key="fixed_2yr", field="rate"
    )
    assert sensor.unique_id == "abc123_fixed_2yr_rate"


def test_sensor_name() -> None:
    """Test sensor name is a human-readable suffix for the group and field."""
    sensor = _make_sensor({}, group_key="fixed_2yr", field="rate")
    assert sensor.name == "Fixed 2yr Rate"


def test_sensor_has_entity_name() -> None:
    """Test sensor has entity name enabled."""
    sensor = _make_sensor({}, group_key="fixed_2yr", field="rate")
    assert sensor.has_entity_name is True


def test_sensor_suggested_display_precision() -> None:
    """Test sensor suggested display precision is 2."""
    sensor = _make_sensor({}, group_key="fixed_2yr", field="rate")
    assert sensor.suggested_display_precision == 2


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------
def test_sensor_extra_state_attributes() -> None:
    """Test extra_state_attributes returns the group's full data plus last_updated."""
    data = {
        "fixed_2yr": {
            "rate": 4.47,
            "lender": "Nationwide",
            "aprc": 6.3,
            "product_fees": 999.0,
            "rate_type": "Fixed",
            "initial_term_years": 2,
            "max_ltv": 60,
            "monthly_payment": 1123.59,
        },
        "last_updated": "2026-07-26T12:00:00Z",
    }
    sensor = _make_sensor(data, group_key="fixed_2yr", field="rate")
    attrs = sensor.extra_state_attributes

    assert attrs["rate"] == 4.47
    assert attrs["lender"] == "Nationwide"
    assert attrs["aprc"] == 6.3
    assert attrs["product_fees"] == 999.0
    assert attrs["rate_type"] == "Fixed"
    assert attrs["initial_term_years"] == 2
    assert attrs["max_ltv"] == 60
    assert attrs["monthly_payment"] == 1123.59
    assert attrs["last_updated"] == "2026-07-26T12:00:00Z"


def test_sensor_unavailable_when_group_missing() -> None:
    """Test sensor native_value is None when its group is missing."""
    sensor = _make_sensor({"last_updated": "2026-07-26T12:00:00Z"}, field="rate")
    assert sensor.native_value is None


def test_sensor_unavailable_when_coordinator_data_none() -> None:
    """Test sensor native_value is None when coordinator data is None."""
    sensor = _make_sensor(None, field="rate")
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Multiple config entries
# ---------------------------------------------------------------------------
def test_sensor_multiple_entries() -> None:
    """Test sensors from different config entries have different unique_ids."""
    data = {
        "fixed_2yr": {"rate": 4.47, "lender": "Nationwide", "monthly_payment": 1123.59},
        "last_updated": "2026-07-26T12:00:00Z",
    }
    sensor_a = _make_sensor(data, entry_id="entry_a", group_key="fixed_2yr", field="rate")
    sensor_b = _make_sensor(data, entry_id="entry_b", group_key="fixed_2yr", field="rate")

    assert sensor_a.unique_id == "entry_a_fixed_2yr_rate"
    assert sensor_b.unique_id == "entry_b_fixed_2yr_rate"
    assert sensor_a.unique_id != sensor_b.unique_id


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_async_setup_entry_creates_three_sensors_per_group() -> None:
    """Test async_setup_entry creates three sensors for each group in coordinator data."""
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.data = {
        "fixed_2yr": {
            "rate": 4.47,
            "lender": "Nationwide",
            "monthly_payment": 1123.59,
        },
        "variable_2yr": {
            "rate": 4.13,
            "lender": "Nationwide BS",
            "monthly_payment": 1123.59,
        },
        "last_updated": "2026-07-26T12:00:00Z",
    }
    hass.data = {"ha_mortgage_rates": {"entry-1": coordinator}}

    entry = MagicMock()
    entry.entry_id = "entry-1"

    async_add_entities = MagicMock()
    await async_setup_entry(hass, entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 6  # 2 groups x 3 fields

    unique_ids = {e.unique_id for e in entities}
    expected = {
        "entry-1_fixed_2yr_rate",
        "entry-1_fixed_2yr_lender",
        "entry-1_fixed_2yr_monthly_payment",
        "entry-1_variable_2yr_rate",
        "entry-1_variable_2yr_lender",
        "entry-1_variable_2yr_monthly_payment",
    }
    assert unique_ids == expected

    # Verify names are human-readable.
    names = {e.name for e in entities}
    assert "Fixed 2yr Rate" in names
    assert "Fixed 2yr Lender" in names
    assert "Fixed 2yr Monthly Payment" in names
    assert "Variable 2yr Rate" in names
    assert "Variable 2yr Lender" in names
    assert "Variable 2yr Monthly Payment" in names
