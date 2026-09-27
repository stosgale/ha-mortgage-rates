"""Tests for the lender-specific rate sources (HSBC)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ha_mortgage_rates.lenders import (
    LENDER_SOURCES,
    _first_number,
    _parse_initial_term,
    _parse_rate_type,
    fetch_hsbc_products,
    parse_hsbc_products,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hsbc_switch_rates.html"


@pytest.fixture
def hsbc_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _products(hsbc_html: str) -> list[dict]:
    return parse_hsbc_products(hsbc_html)


def test_parse_hsbc_products_returns_products(hsbc_html: str) -> None:
    products = _products(hsbc_html)
    assert products
    for product in products:
        assert product["lender"] == "HSBC"
        assert isinstance(product["rate"], float)
        assert product["rate"] > 0
        assert product["rate_type"] in ("Fixed", "Variable", "Tracker")
        assert isinstance(product["max_ltv"], int)
        assert product["max_ltv"] in (60, 65, 70, 75, 80, 85, 90, 95)


def test_parse_hsbc_products_deduplicates(hsbc_html: str) -> None:
    products = _products(hsbc_html)
    keys = [
        (p["max_ltv"], p["rate_type"], p["initial_term_years"], p["rate"])
        for p in products
    ]
    assert len(keys) == len(set(keys))


def test_parse_hsbc_products_excludes_premier_and_btl(hsbc_html: str) -> None:
    products = _products(hsbc_html)
    for product in products:
        assert product["product_fees"] is None or product["product_fees"] >= 0
    # The fixture's 60% band contains "2 Year Fixed Premier Standard" (4.66%)
    # and several "Buy To Let" rows; none should survive parsing.
    rates_60 = {p["rate"] for p in products if p["max_ltv"] == 60}
    assert 4.66 not in rates_60  # Premier Standard is excluded
    assert 5.06 not in rates_60  # 2 Year Fixed Fee Saver BTL is excluded
    assert 4.69 in rates_60  # 2 Year Fixed Standard (residential) is kept


def test_parse_hsbc_products_two_year_fixed_standard(hsbc_html: str) -> None:
    products = _products(hsbc_html)
    fixed_2yr_60 = [
        p
        for p in products
        if p["rate_type"] == "Fixed" and p["initial_term_years"] == 2 and p["max_ltv"] == 60
    ]
    cheapest = min(p["rate"] for p in fixed_2yr_60)
    assert cheapest == pytest.approx(4.69)
    assert {p["rate"] for p in fixed_2yr_60} == {4.69, 4.94}


def test_parse_hsbc_products_tracker_is_kept() -> None:
    html = (
        "<table><caption><strong>60% Maximum Loan to Value (LTV)</strong></caption>"
        "<tbody>"
        '<tr><th scope="col">Mortgage</th><th scope="col">Initial interest rate</th>'
        "<th>Revert</th><th>Period</th><th>APRC</th><th>Fee</th></tr>"
        '<tr><th scope="row">2 Year Term Tracker Fee Saver</th>'
        "<td>5.09% tracker</td><td>6.24%</td>"
        "<td>2 Years tracker until 31.10.2028</td><td>6.30% APRC</td>"
        "<td>&pound;0</td><td>10%</td><td>&pound;0</td><td>&pound;2,000,000</td></tr>"
        "</tbody></table>"
    )
    products = parse_hsbc_products(html)
    assert len(products) == 1
    assert products[0]["rate_type"] == "Tracker"
    assert products[0]["initial_term_years"] == 2
    assert products[0]["rate"] == pytest.approx(5.09)


def _fake_session(hsbc_html: str) -> MagicMock:
    response = MagicMock()
    response.text = AsyncMock(return_value=hsbc_html)
    session = MagicMock()
    session.get = AsyncMock(return_value=response)
    return session


@pytest.mark.asyncio
async def test_fetch_hsbc_products_filters_by_ltv(hsbc_html: str) -> None:
    session = _fake_session(hsbc_html)
    products = await fetch_hsbc_products(session, {}, loan_to_value=58)
    assert products
    assert all(p["max_ltv"] >= 58 for p in products)
    cheapest_2yr = min(
        p["rate"]
        for p in products
        if p["rate_type"] == "Fixed" and p["initial_term_years"] == 2
    )
    assert cheapest_2yr == pytest.approx(4.69)
    session.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_fetch_hsbc_products_high_ltv_drops_low_bands(hsbc_html: str) -> None:
    session = _fake_session(hsbc_html)
    products = await fetch_hsbc_products(session, {}, loan_to_value=80)
    assert products
    assert all(p["max_ltv"] >= 80 for p in products)
    assert all(p["max_ltv"] != 60 for p in products)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4.69%", 4.69),
        ("4.69% fixed", 4.69),
        ("6.40% APRC", 6.40),
        ("£1,999", 1999.0),
        ("n/a", None),
    ],
)
def test_first_number(text: str, expected: float | None) -> None:
    assert _first_number(text) == expected


@pytest.mark.parametrize(
    ("name", "rate_type", "term"),
    [
        ("2 Year Fixed Standard", "Fixed", 2),
        ("5 Year Fixed Fee Saver", "Fixed", 5),
        ("10 Year Fixed Premier Standard", "Fixed", 10),
        ("2 Year Term Tracker Standard", "Tracker", 2),
        ("Standard Variable Rate", "Variable", None),
    ],
)
def test_product_name_parsing(name: str, rate_type: str, term: int | None) -> None:
    assert _parse_rate_type(name) == rate_type
    assert _parse_initial_term(name) == term


def test_lender_sources_registry_has_hsbc() -> None:
    assert "hsbc" in LENDER_SOURCES
    assert LENDER_SOURCES["hsbc"] is fetch_hsbc_products
