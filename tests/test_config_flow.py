"""Tests for the UK Mortgage Rates config flow."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
import voluptuous as vol

sys.path.insert(0, "/tmp/opencode/ha-mortgage-rates")

# ---------------------------------------------------------------------------
# Mock Home Assistant dependencies – MUST happen before any HA imports.
# We create real module objects (via types.ModuleType) so the import system
# can resolve them as packages, then attach MagicMock attributes for the
# specific things each HA submodule exports.
# ---------------------------------------------------------------------------

class _MockModule(types.ModuleType):
    """A module object that auto-creates MagicMock attributes on demand."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.__path__: list[str] = []

    def __getattr__(self, name: str):  # type: ignore[misc]
        if name.startswith("_"):
            raise AttributeError(name)
        attr = MagicMock()
        setattr(self, name, attr)
        return attr


def _install_fake_module(name: str) -> _MockModule:
    """Create and install a fake module in sys.modules."""
    mod = _MockModule(name)
    sys.modules[name] = mod
    return mod


# Install the homeassistant package and all sub-modules that the custom
# component imports (either directly or transitively).
# Only fake the modules that MUST be mock namespaces. Everything else is
# pre-imported in conftest.py (which loads first) so the real modules
# survive the root shadow and remain importable by other test files.
# homeassistant.config_entries is kept real (pre-imported) so that
# ConfigEntry remains a real class for test_coordinator fixtures.
_FAKE_MODULES = [
    "homeassistant",
    "homeassistant.helpers",
]

for _name in _FAKE_MODULES:
    _install_fake_module(_name)

# Shared state for duplicate-detection tests
_seen_unique_ids: set[str] = set()


class _AbortFlow(Exception):
    """Stand-in for homeassistant.config_entries.AbortFlow."""


class _MockConfigFlow:
    """Stand-in for homeassistant.config_entries.ConfigFlow."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init_subclass__(cls, **kwargs):  # noqa: B027
        """Consume subclass kwargs (e.g. domain=DOMAIN) without error."""
        pass

    def __init__(self):
        self._unique_id = None

    def async_show_form(self, *, step_id, data_schema, errors=None):
        return {
            "type": "form",
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(self, *, title, data):
        return {"type": "create_entry", "title": title, "data": data}

    async def async_set_unique_id(self, unique_id):
        self._unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        if self._unique_id in _seen_unique_ids:
            raise _AbortFlow("already_configured")
        _seen_unique_ids.add(self._unique_id)


# Install well-known HA type mocks on the fake modules.
_ce = sys.modules["homeassistant.config_entries"]
_ce.ConfigFlow = _MockConfigFlow
_ce.FlowResult = dict
_ce.AbortFlow = _AbortFlow

_ha = sys.modules["homeassistant"]
_ha.async_create_task = MagicMock()

del _ce, _sel, _name

# ---------------------------------------------------------------------------
# HA-domain imports – safe once the homeassistant namespace is mocked.
# ---------------------------------------------------------------------------
from custom_components.ha_mortgage_rates.config_flow import MortgageRatesConfigFlow
from custom_components.ha_mortgage_rates.const import (
    CONF_MORTGAGE_AMOUNT,
    CONF_PROPERTY_VALUE,
    CONF_PURPOSE,
    CONF_TERM,
    DEFAULT_TERM,
    PURPOSES,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    """Reset shared flow state before each test."""
    _seen_unique_ids.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def make_flow(user_input=None):
    """Instantiate the config flow and run *async_step_user*."""
    flow = MortgageRatesConfigFlow()
    return await flow.async_step_user(user_input)


def _valid_data(**overrides):
    """Return a dict of valid config-flow input, optionally overridden."""
    data: dict = {
        CONF_PROPERTY_VALUE: 200000,
        CONF_MORTGAGE_AMOUNT: 150000,
        CONF_PURPOSE: "remortgage",
        CONF_TERM: 25,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMortgageRatesConfigFlow:
    """Tests for MortgageRatesConfigFlow."""

    async def test_form_structure(self):
        """Verify async_step_user returns a form with expected schema fields."""
        result = await make_flow()
        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["errors"] == {}

        schema = result["data_schema"]
        validators = schema.schema
        assert len(validators) == 4

        required_keys = {k for k in validators if isinstance(k, vol.Required)}
        optional_keys = {k for k in validators if isinstance(k, vol.Optional)}
        assert len(required_keys) == 3
        assert len(optional_keys) == 1

        (term_marker,) = optional_keys
        assert term_marker.schema == CONF_TERM
        term_default = term_marker.default
        if callable(term_default):
            term_default = term_default()
        assert term_default == DEFAULT_TERM

    async def test_valid_entry(self):
        """Submit valid data and verify a create_entry result."""
        data = _valid_data()
        result = await make_flow(data)
        assert result["type"] == "create_entry"
        assert result["title"] == "Remortgage (75% LTV, 25yr)"
        assert result["data"] == data

    @pytest.mark.parametrize(
        ("prop", "mort", "ltv"),
        [
            (200000, 150000, 75),
            (300000, 60000, 20),
        ],
    )
    async def test_ltv_computation(self, prop, mort, ltv):
        """Verify LTV = int(mortgage_amount / property_value * 100)."""
        data = _valid_data(property_value=prop, mortgage_amount=mort)
        result = await make_flow(data)
        assert result["type"] == "create_entry"
        assert f"({ltv}% LTV," in result["title"]

    async def test_invalid_property_value_zero(self):
        """property_value = 0  -> error on property_value."""
        result = await make_flow(_valid_data(property_value=0))
        assert result["type"] == "form"
        assert result["errors"].get(CONF_PROPERTY_VALUE) == "invalid_value"

    async def test_invalid_mortgage_amount_zero(self):
        """mortgage_amount = 0  -> error on mortgage_amount."""
        result = await make_flow(_valid_data(mortgage_amount=0))
        assert result["type"] == "form"
        assert result["errors"].get(CONF_MORTGAGE_AMOUNT) == "invalid_value"

    async def test_invalid_mortgage_greater_than_property(self):
        """mortgage_amount > property_value -> error."""
        result = await make_flow(_valid_data(mortgage_amount=250000))
        assert result["type"] == "form"
        assert result["errors"].get(CONF_MORTGAGE_AMOUNT) == "invalid_amount"

    async def test_invalid_term_too_low(self):
        """term = 0  -> error on term."""
        result = await make_flow(_valid_data(term=0))
        assert result["type"] == "form"
        assert result["errors"].get(CONF_TERM) == "invalid_value"

    async def test_invalid_term_too_high(self):
        """term = 41 -> error on term."""
        result = await make_flow(_valid_data(term=41))
        assert result["type"] == "form"
        assert result["errors"].get(CONF_TERM) == "invalid_value"

    async def test_default_term(self):
        """Omitting term should default to 25 in the entry title."""
        result = await make_flow(
            {
                CONF_PROPERTY_VALUE: 200000,
                CONF_MORTGAGE_AMOUNT: 150000,
                CONF_PURPOSE: "remortgage",
            }
        )
        assert result["type"] == "create_entry"
        assert "25yr" in result["title"]

    async def test_duplicate_detection(self):
        """Submitting identical data twice should abort the second attempt."""
        data = _valid_data()
        first = await make_flow(data)
        assert first["type"] == "create_entry"

        with pytest.raises(_AbortFlow, match="already_configured"):
            await make_flow(data)

    @pytest.mark.parametrize("purpose", PURPOSES)
    async def test_all_purposes(self, purpose):
        """Every purpose value should create a valid entry."""
        data = _valid_data(purpose=purpose)
        result = await make_flow(data)
        assert result["type"] == "create_entry"

    async def test_title_format(self):
        """Verify title format: 'Remortgage (75% LTV, 25yr)'."""
        result = await make_flow(
            _valid_data(
                property_value=200000,
                mortgage_amount=150000,
                purpose="remortgage",
                term=25,
            )
        )
        assert result["type"] == "create_entry"
        assert result["title"] == "Remortgage (75% LTV, 25yr)"
