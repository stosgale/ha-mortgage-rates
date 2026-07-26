"""Fixtures for the ha_mortgage_rates tests.

NOTE: The homeassistant sub-module imports below must happen at module level
in conftest.py (which loads first during collection) so they are cached in
sys.modules before test_config_flow.py shadows the top-level homeassistant
namespace with its _MockModule. Without this, test_sensor.py's imports of
homeassistant.components.sensor (and sensor.py's own HA imports) would fail
when the tests are collected together.
"""
from __future__ import annotations

# Eagerly import real HA modules so they survive test_config_flow's
# module-level sys.modules replacement of the homeassistant namespace.
# The real modules stay in sys.modules and remain importable even after
# the top-level key is overwritten with a _MockModule.
import homeassistant.components.sensor  # noqa: F401
import homeassistant.config_entries  # noqa: F401
import homeassistant.const  # noqa: F401
import homeassistant.core  # noqa: F401
import homeassistant.exceptions  # noqa: F401
import homeassistant.helpers.aiohttp_client  # noqa: F401
import homeassistant.helpers.update_coordinator  # noqa: F401
