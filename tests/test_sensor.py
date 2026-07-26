"""Tests for the HA Mortgage Rates sensor."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# Mock the entity_platform module before importing sensor.py so that
# AddConfigEntryEntitiesCallback (added in HA 2025.x) is available
# even on older HA installations used for CI/testing.
_entity_platform_mock = MagicMock()
_entity_platform_mock.AddConfigEntryEntitiesCallback = MagicMock()
sys.modules["homeassistant.helpers.entity_platform"] = _entity_platform_mock

from homeassistant.components.sensor import SensorStateClass  # noqa: E402

from custom_components.ha_mortgage_rates.sensor import (  # noqa: E402
    MortgageRateSensor,
    SENSOR_DESCRIPTIONS,
)


def _make_sensor(
    coordinator_data: dict | None = None,
    entry_id: str = "abc123",
) -> MortgageRateSensor:
    """Build a MortgageRateSensor with mocked coordinator and config entry."""
    coordinator = MagicMock()
    coordinator.data = coordinator_data

    entry = MagicMock()
    entry.entry_id = entry_id

    return MortgageRateSensor(coordinator, SENSOR_DESCRIPTIONS[0], entry)


# ---------------------------------------------------------------------------
# Test 1 — native_value
# ---------------------------------------------------------------------------
def test_sensor_state() -> None:
    """Test sensor native_value matches best_rate from coordinator data."""
    sensor = _make_sensor({"best_rate": 4.14})
    assert sensor.native_value == 4.14


# ---------------------------------------------------------------------------
# Test 2 — extra_state_attributes
# ---------------------------------------------------------------------------
def test_sensor_attributes() -> None:
    """Test extra_state_attributes returns all 8 expected keys with correct values."""
    data = {
        "best_rate": 4.14,
        "lender": "Nationwide",
        "aprc": 5.2,
        "product_fees": 999.0,
        "rate_type": "Fixed",
        "initial_term_years": 5,
        "max_ltv": 60,
        "monthly_payment": 850.50,
        "last_updated": "2026-07-26T12:00:00Z",
    }
    sensor = _make_sensor(data)
    attrs = sensor.extra_state_attributes

    assert attrs["lender"] == "Nationwide"
    assert attrs["aprc"] == 5.2
    assert attrs["product_fees"] == 999.0
    assert attrs["rate_type"] == "Fixed"
    assert attrs["initial_term_years"] == 5
    assert attrs["max_ltv"] == 60
    assert attrs["monthly_payment"] == 850.50
    assert attrs["last_updated"] == "2026-07-26T12:00:00Z"

    expected_keys = {
        "lender",
        "aprc",
        "product_fees",
        "rate_type",
        "initial_term_years",
        "max_ltv",
        "monthly_payment",
        "last_updated",
    }
    assert set(attrs.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Test 3 — native_unit_of_measurement
# ---------------------------------------------------------------------------
def test_sensor_native_unit_of_measurement() -> None:
    """Test sensor unit of measurement is percent."""
    sensor = _make_sensor({})
    assert sensor.entity_description.native_unit_of_measurement == "%"


# ---------------------------------------------------------------------------
# Test 4 — state_class
# ---------------------------------------------------------------------------
def test_sensor_state_class() -> None:
    """Test sensor state class is MEASUREMENT."""
    sensor = _make_sensor({})
    assert sensor.entity_description.state_class == SensorStateClass.MEASUREMENT


# ---------------------------------------------------------------------------
# Test 5 — unique_id
# ---------------------------------------------------------------------------
def test_sensor_unique_id() -> None:
    """Test sensor unique_id follows entry.entry_id pattern."""
    sensor = _make_sensor({}, entry_id="abc123")
    assert sensor.unique_id == "abc123_best_rate"


# ---------------------------------------------------------------------------
# Test 6 — icon
# ---------------------------------------------------------------------------
def test_sensor_icon() -> None:
    """Test sensor icon is mdi:percent."""
    sensor = _make_sensor({})
    assert sensor.icon == "mdi:percent"


# ---------------------------------------------------------------------------
# Test 7 — has_entity_name
# ---------------------------------------------------------------------------
def test_sensor_has_entity_name() -> None:
    """Test sensor has entity name enabled."""
    sensor = _make_sensor({})
    assert sensor.has_entity_name is True


# ---------------------------------------------------------------------------
# Test 8 — suggested_display_precision
# ---------------------------------------------------------------------------
def test_sensor_suggested_display_precision() -> None:
    """Test sensor suggested display precision is 2."""
    sensor = _make_sensor({})
    assert sensor.entity_description.suggested_display_precision == 2


# ---------------------------------------------------------------------------
# Test 9 — unavailable / None best_rate
# ---------------------------------------------------------------------------
def test_sensor_unavailable() -> None:
    """Test sensor native_value is None when best_rate is None."""
    sensor = _make_sensor({"best_rate": None, "lender": "Nationwide"})
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# Test 10 — missing keys in coordinator data
# ---------------------------------------------------------------------------
def test_sensor_missing_attributes() -> None:
    """Test extra_state_attributes returns None (not KeyError) for missing keys."""
    # Empty dict — every key is missing
    sensor = _make_sensor({})
    attrs = sensor.extra_state_attributes
    for key in (
        "lender",
        "aprc",
        "product_fees",
        "rate_type",
        "initial_term_years",
        "max_ltv",
        "monthly_payment",
        "last_updated",
    ):
        assert attrs[key] is None

    # coordinator.data is None — the property falls back to {}
    sensor_none = _make_sensor(None)
    attrs_none = sensor_none.extra_state_attributes
    for key in (
        "lender",
        "aprc",
        "product_fees",
        "rate_type",
        "initial_term_years",
        "max_ltv",
        "monthly_payment",
        "last_updated",
    ):
        assert attrs_none[key] is None


# ---------------------------------------------------------------------------
# Test 11 — multiple entries
# ---------------------------------------------------------------------------
def test_sensor_multiple_entries() -> None:
    """Test sensors from different config entries have different unique_ids."""
    sensor_a = _make_sensor({}, entry_id="entry_a")
    sensor_b = _make_sensor({}, entry_id="entry_b")

    assert sensor_a.unique_id == "entry_a_best_rate"
    assert sensor_b.unique_id == "entry_b_best_rate"
    assert sensor_a.unique_id != sensor_b.unique_id
