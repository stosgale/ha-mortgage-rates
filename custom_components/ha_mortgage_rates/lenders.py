"""Lender-specific mortgage rate sources.

The integration's primary data source is the Moneyfacts comparison chart.
Moneyfacts is a useful aggregate, but it does not always carry the very
latest rate a lender publishes on its own website - and for some lenders it
only carries a subset of products.  HSBC is a concrete example: Moneyfacts
listed HSBC's cheapest 60% LTV two-year fixed at 5.09% while HSBC's own
existing-customer switch page advertised 4.69% for the equivalent product.

For every tracked lender that has a dedicated source defined in
``LENDER_SOURCES`` the coordinator fetches the lender's own published rate
page and *replaces* the Moneyfacts products for that lender, so the
per-lender sensors (and the overall cheapest rate) reflect what the lender
actually offers.

Each source is an async callable with the signature::

    async def source(session, headers, loan_to_value) -> list[dict]

and must return products using the same dictionary shape as the Moneyfacts
parser in :mod:`coordinator`: ``lender``, ``rate``, ``aprc``,
``product_fees``, ``monthly_payment``, ``rate_type``, ``initial_term_years``
and ``max_ltv``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

import aiohttp
import async_timeout
from bs4 import BeautifulSoup

from .const import REQUEST_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

# HSBC's "existing customers -> switch" rate page.  It is server-rendered
# (no JavaScript required) and lists the residential and buy-to-let fixed,
# tracker and variable rates for every LTV band.
HSBC_RATES_URL = "https://www.hsbc.co.uk/mortgages/existing-customers/switch/rates/"

# The LTV bands HSBC publishes (60/65/70/75/80/85/90/95), the same bands its
# own page tells customers to use ("if your LTV falls between two bands, use
# the highest band").
HSBC_LTV_BANDS: tuple[int, ...] = (60, 65, 70, 75, 80, 85, 90, 95)

# Products that are not comparable with the Moneyfacts residential
# remortgage chart: buy-to-let deals are only available for a BTL mortgage,
# and "Premier" deals require an HSBC Premier relationship.  Moneyfacts
# itself lists no Premier products, so excluding them keeps the direct HSBC
# source consistent with the rest of the chart.
_HSBC_EXCLUDED_PRODUCT_MARKERS: tuple[str, ...] = ("buy to let", "premier")

_HSBC_LTV_CAPTION_RE = re.compile(r"(\d+)\s*%\s*maximum\s+loan\s+to\s+value", re.IGNORECASE)
_HSBC_TERM_RE = re.compile(r"(\d+)\s*year", re.IGNORECASE)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _first_number(text: str) -> float | None:
    """Return the first number found in *text* (e.g. "4.69% fixed" -> 4.69)."""
    match = _NUMBER_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_rate_type(product_name: str) -> str | None:
    """Map an HSBC product name to a Moneyfacts-style rate type."""
    lowered = product_name.lower()
    if "tracker" in lowered:
        return "Tracker"
    if "fixed" in lowered:
        return "Fixed"
    if "variable" in lowered:
        return "Variable"
    return None


def _parse_initial_term(product_name: str) -> int | None:
    """Extract the fixed/tracker period in years from a product name."""
    match = _HSBC_TERM_RE.search(product_name)
    if not match:
        return None
    return int(match.group(1))


def _is_excluded(name: str) -> bool:
    """Return True for products that are not residential remortgage deals."""
    lowered = name.lower()
    return any(marker in lowered for marker in _HSBC_EXCLUDED_PRODUCT_MARKERS)


def parse_hsbc_products(html: str) -> list[dict[str, Any]]:
    """Parse HSBC's published rate tables into Moneyfacts-shaped products.

    The page repeats every LTV band in several tables (responsive/variant
    layouts), so duplicate ``(ltv, product, rate)`` rows are de-duplicated.
    """
    soup = BeautifulSoup(html, "html.parser")
    products: list[dict[str, Any]] = []
    seen: set[tuple[int, str, float]] = set()

    for table in soup.find_all("table"):
        caption = table.find("caption")
        if caption is None:
            continue
        caption_text = caption.get_text(" ", strip=True)
        ltv_match = _HSBC_LTV_CAPTION_RE.search(caption_text)
        if not ltv_match:
            continue
        max_ltv = int(ltv_match.group(1))
        if max_ltv not in HSBC_LTV_BANDS:
            continue

        for row in table.find_all("tr"):
            name_cell = row.find("th", attrs={"scope": "row"})
            if name_cell is None:
                continue
            name = name_cell.get_text(" ", strip=True)
            if not name or _is_excluded(name):
                continue

            cells = row.find_all("td")
            if len(cells) < 5:
                continue

            rate = _first_number(cells[0].get_text(" ", strip=True))
            if rate is None:
                continue
            aprc = _first_number(cells[3].get_text(" ", strip=True))
            product_fees = _first_number(cells[4].get_text(" ", strip=True))

            dedupe_key = (max_ltv, name, rate)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            products.append(
                {
                    "lender": "HSBC",
                    "rate": rate,
                    "aprc": aprc,
                    "product_fees": product_fees,
                    "monthly_payment": None,
                    "rate_type": _parse_rate_type(name),
                    "initial_term_years": _parse_initial_term(name),
                    "max_ltv": max_ltv,
                }
            )

    return products


async def fetch_hsbc_products(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    loan_to_value: int | None = None,
) -> list[dict[str, Any]]:
    """Fetch and parse HSBC's published rates page."""
    async with async_timeout.timeout(REQUEST_TIMEOUT_SECONDS):
        response = await session.get(
            HSBC_RATES_URL, headers=headers, raise_for_status=True
        )
        html = await response.text()

    products = parse_hsbc_products(html)
    if loan_to_value is not None:
        # Only keep products the borrower actually qualifies for: a product
        # with a 60% maximum LTV is available to a 58% LTV borrower, so a
        # band is applicable when its maximum LTV is at or above the loan's
        # LTV.  This mirrors HSBC's own "use the highest band" guidance.
        products = [p for p in products if p["max_ltv"] >= loan_to_value]

    _LOGGER.info(
        "HSBC direct source: parsed %d residential products (LTV>=%s)",
        len(products),
        loan_to_value,
    )
    return products


# Registry of tracked-lender name -> dedicated source.  Keys are matched
# against the lower-cased tracked-lender tokens from the config entry.
LENDER_SOURCES: dict[str, Callable[..., Awaitable[list[dict[str, Any]]]]] = {
    "hsbc": fetch_hsbc_products,
}
