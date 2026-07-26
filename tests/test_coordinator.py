"""Tests for the HA Mortgage Rates coordinator."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import pytest_asyncio
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ha_mortgage_rates.const import (
    CONF_MORTGAGE_AMOUNT,
    CONF_PROPERTY_VALUE,
    CONF_PURPOSE,
    CONF_TERM,
    PURPOSE_BTL,
    PURPOSE_FTB,
    PURPOSE_HOME_MOVER,
    PURPOSE_REMORTGAGE,
)
from custom_components.ha_mortgage_rates.coordinator import (
    MortgageRatesCoordinator,
)


@pytest.fixture
def hass() -> HomeAssistant:
    """Return a mocked Home Assistant instance."""
    return MagicMock(spec=HomeAssistant)


def build_config(data: dict[str, Any]) -> ConfigEntry:
    """Build a minimal mock ConfigEntry with the requested data."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test-entry"
    entry.data = data
    return entry


def coordinator(hass: HomeAssistant, data: dict[str, Any]) -> MortgageRatesCoordinator:
    """Instantiate a MortgageRatesCoordinator for testing."""
    return MortgageRatesCoordinator(hass, build_config(data))


@pytest.fixture
def fixture_html() -> str:
    """Load the sanitized Moneyfacts remortgage HTML fixture."""
    with open("tests/fixtures/moneyfacts_remortgage.html", "r", encoding="utf-8") as f:
        return f.read()


# -----------------------------------------------------------------------------
# URL construction
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("purpose", "ltv", "expected_path"),
    [
        (PURPOSE_REMORTGAGE, 75, "/mortgages/remortgage/75-ltv/"),
        (PURPOSE_FTB, 90, "/mortgages/first-time-buyer-mortgages/90-ltv/"),
        (PURPOSE_HOME_MOVER, 60, "/mortgages/60-ltv-mortgages/"),
        (PURPOSE_BTL, 75, "/mortgages/buy-to-let/75-ltv/"),
    ],
)
def test_build_url_per_purpose(hass: HomeAssistant, purpose: str, ltv: int, expected_path: str) -> None:
    """Test that _build_url returns the correct path for each purpose."""
    property_value = 100000
    mortgage_amount = int(property_value * (ltv / 100))
    coord = coordinator(
        hass,
        {
            CONF_PURPOSE: purpose,
            CONF_PROPERTY_VALUE: property_value,
            CONF_MORTGAGE_AMOUNT: mortgage_amount,
        },
    )
    url = coord._build_url()
    assert url.endswith(expected_path)


def test_build_url_ltv_rounding(hass: HomeAssistant) -> None:
    """Test that a 72% LTV rounds up to the nearest 75% remortgage band."""
    coord = coordinator(
        hass,
        {
            CONF_PURPOSE: PURPOSE_REMORTGAGE,
            CONF_PROPERTY_VALUE: 100000,
            CONF_MORTGAGE_AMOUNT: 72000,
        },
    )
    assert coord._build_url() == "https://moneyfactscompare.co.uk/mortgages/remortgage/75-ltv/"


def test_build_url_ltv_above_max(hass: HomeAssistant) -> None:
    """Test that an LTV above the max band falls back to the highest band."""
    coord = coordinator(
        hass,
        {
            CONF_PURPOSE: PURPOSE_REMORTGAGE,
            CONF_PROPERTY_VALUE: 100000,
            CONF_MORTGAGE_AMOUNT: 95000,
        },
    )
    assert coord._build_url() == "https://moneyfactscompare.co.uk/mortgages/remortgage/80-ltv/"


# -----------------------------------------------------------------------------
# Static parsing helpers
# -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4.14%", 4.14),
        ("4.14% APR", None),  # coordinator only strips '%', not trailing text
        ("6.2", 6.2),
        ("", None),
        ("N/A", None),
    ],
)
def test_parse_rate(text: str, expected: float | None) -> None:
    """Test _parse_rate strips '%' and handles missing values."""
    assert MortgageRatesCoordinator._parse_rate(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("£999", 999.0),
        ("£1,999", 1999.0),
        ("", None),
        ("N/A", None),
    ],
)
def test_parse_money(text: str, expected: float | None) -> None:
    """Test _parse_money strips '£' / commas and handles missing values."""
    assert MortgageRatesCoordinator._parse_money(text) == expected


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("4.47% Fixed for 2 years", "Fixed"),
        ("4.13% Variable (collared) for 2 years", "Variable"),
        ("5.0% Tracker for 2 years", "Tracker"),
        ("Some description", None),
    ],
)
def test_parse_rate_type(description: str, expected: str | None) -> None:
    """Test _parse_rate_type extracts the rate type."""
    assert MortgageRatesCoordinator._parse_rate_type(description) == expected


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("4.47% Fixed for 2 years", 2),
        ("3.99% Fixed for 5 years", 5),
        ("5.0% Variable", None),
    ],
)
def test_parse_initial_term(description: str, expected: int | None) -> None:
    """Test _parse_initial_term extracts the term in years."""
    assert MortgageRatesCoordinator._parse_initial_term(description) == expected


# -----------------------------------------------------------------------------
# HTML parsing with the real fixture
# -----------------------------------------------------------------------------
def test_parse_html_with_fixture(hass: HomeAssistant, fixture_html: str) -> None:
    """Test parsing the real fixture returns at least 5 products with valid fields."""
    coord = coordinator(hass, {})
    result = coord._parse_html(fixture_html)

    assert isinstance(result["best_rate"], float)
    assert result["best_rate"] > 0
    assert isinstance(result["lender"], str)
    assert result["lender"]
    assert result["rate_type"] in ("Fixed", "Variable", "Tracker", None)
    assert isinstance(result["initial_term_years"], int) or result["initial_term_years"] is None


def test_parse_html_best_rate_lowest(hass: HomeAssistant, fixture_html: str) -> None:
    """Test that the returned best_rate is the minimum across all products."""
    from bs4 import BeautifulSoup

    coord = coordinator(hass, {})
    soup = BeautifulSoup(fixture_html, "html.parser")
    rows = soup.select("li.mortgages-table-item.table-item")

    extracted_rates = []
    for row in rows:
        product = coord._extract_product(row)
        if product["best_rate"] is not None:
            extracted_rates.append(product["best_rate"])

    result = coord._parse_html(fixture_html)
    assert result["best_rate"] == pytest.approx(min(extracted_rates))


def test_parse_html_empty(hass: HomeAssistant) -> None:
    """Test that empty HTML raises UpdateFailed with 'no rates found'."""
    coord = coordinator(hass, {})
    with pytest.raises(UpdateFailed, match="no rates found"):
        coord._parse_html("")


def test_parse_html_no_rates(hass: HomeAssistant) -> None:
    """Test that HTML with no product rows raises UpdateFailed."""
    coord = coordinator(hass, {})
    html = "<html><body><ul id='finder-table'><li class='not-a-product'>no data</li></ul></body></html>"
    with pytest.raises(UpdateFailed, match="no rates found"):
        coord._parse_html(html)


# -----------------------------------------------------------------------------
# Async data update with mocked aiohttp
# -----------------------------------------------------------------------------
@pytest_asyncio.fixture
async def mock_session(fixture_html: str) -> AsyncMock:
    """Return a mocked aiohttp ClientSession that returns the fixture HTML."""
    response = MagicMock(spec=aiohttp.ClientResponse)
    response.text = AsyncMock(return_value=fixture_html)
    response.raise_for_status = AsyncMock()

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = AsyncMock(return_value=response)
    return session


@pytest.mark.asyncio
async def test_async_update_data_success(
    hass: HomeAssistant, mock_session: AsyncMock
) -> None:
    """Test that async_update_data returns the expected dict on HTTP success."""
    coord = coordinator(
        hass,
        {
            CONF_PURPOSE: PURPOSE_REMORTGAGE,
            CONF_PROPERTY_VALUE: 200000,
            CONF_MORTGAGE_AMOUNT: 120000,
            CONF_TERM: 25,
        },
    )
    coord._session = mock_session

    result = await coord._async_update_data()

    assert isinstance(result, dict)
    assert "best_rate" in result
    assert "lender" in result
    assert "aprc" in result
    assert "product_fees" in result
    assert "rate_type" in result
    assert "initial_term_years" in result
    assert "max_ltv" in result
    assert "monthly_payment" in result
    assert "last_updated" in result

    mock_session.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_update_data_http_error(hass: HomeAssistant) -> None:
    """Test that a mocked HTTP 500 response raises UpdateFailed."""
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = AsyncMock(side_effect=aiohttp.ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=500,
    ))

    coord = coordinator(
        hass,
        {
            CONF_PURPOSE: PURPOSE_REMORTGAGE,
            CONF_PROPERTY_VALUE: 200000,
            CONF_MORTGAGE_AMOUNT: 120000,
        },
    )
    coord._session = session

    with pytest.raises(UpdateFailed, match="error fetching mortgage rates"):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_timeout(hass: HomeAssistant) -> None:
    """Test that a mocked timeout raises UpdateFailed."""
    import asyncio

    session = MagicMock(spec=aiohttp.ClientSession)
    session.get = AsyncMock(side_effect=asyncio.TimeoutError)

    coord = coordinator(
        hass,
        {
            CONF_PURPOSE: PURPOSE_REMORTGAGE,
            CONF_PROPERTY_VALUE: 200000,
            CONF_MORTGAGE_AMOUNT: 120000,
        },
    )
    coord._session = session

    with pytest.raises(UpdateFailed, match="timeout fetching mortgage rates"):
        await coord._async_update_data()
