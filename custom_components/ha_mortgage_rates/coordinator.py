"""DataUpdateCoordinator for the HA Mortgage Rates integration.

================================================================================
VALIDATION SPIKE FINDINGS (moneyfactscompare.co.uk)
================================================================================
Fetched pages (2026-07-26) with curl and a browser-like User-Agent:
  - https://moneyfactscompare.co.uk/mortgages/remortgage/
  - https://moneyfactscompare.co.uk/mortgages/remortgage/60-ltv/
  - https://moneyfactscompare.co.uk/mortgages/first-time-buyer-mortgages/
  - https://moneyfactscompare.co.uk/mortgages/moving-home/
  - https://moneyfactscompare.co.uk/mortgages/buy-to-let/

Server-rendered: YES. Product data is present in the initial HTML as a list of
`<li class="mortgages-table-item table-item">` items inside `<ul id="finder-table">`.
No JavaScript execution is required to read the headline fields.

Exact CSS selectors used for extraction (per product row):
  - Container row:          "li.mortgages-table-item.table-item"
  - Lender name:            ".table-item-heading-product-name strong"
  - Initial rate:           first ".table-item-cell .table-item-cell-value strong"
  - APRC:                   ".table-item-cell.aprc .table-item-cell-value strong"
  - Max LTV:                ".table-item-cell.max-ltv-small .table-item-cell-value strong"
  - Product fees:           ".table-item-cell.product-fees .table-item-cell-value strong"
  - Monthly payment:        ".table-item-cell.initial-monthly-payment .table-item-cell-value strong"
  - Rate type & initial term:
      Parsed from the description text in
      ".table-item-cell .table-item-cell-value .small"
      e.g. "4.47% Fixed for 2 years" or "4.13% Variable (collared at 0.38%) for 2 years".
      Rate type regex: (Fixed|Variable|Tracker)
      Term regex:      "for (\\d+) year"

Pagination: NONE observed. The full product list for the selected LTV/purpose is
rendered in one page.

LTV filtering: URL-based, not client-side JS. Each purpose exposes LTV-specific
URLs. Confirmed available bands (manual curl probe):
  - remortgage:              /mortgages/remortgage/{ltv}-ltv/      -> 60, 75, 80
  - first_time_buyer:        /mortgages/first-time-buyer-mortgages/{ltv}-ltv/
                                                                   -> 85, 90, 95, 100
  - home_mover:              /mortgages/{ltv}-ltv-mortgages/       -> 60, 75, 80, 85, 90, 95
  - buy_to_let:              /mortgages/buy-to-let/{ltv}-ltv/      -> 60, 75, 80
The coordinator rounds the computed LTV up to the smallest available band that
still covers the requested loan, falling back to the highest band if the LTV
exceeds the available range.
================================================================================
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import async_timeout
from bs4 import BeautifulSoup

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_MORTGAGE_AMOUNT,
    CONF_PROPERTY_VALUE,
    CONF_PURPOSE,
    CONF_TERM,
    CONF_TRACKED_LENDERS,
    DOMAIN,
    PURPOSE_BTL,
    PURPOSE_FTB,
    PURPOSE_HOME_MOVER,
    PURPOSE_REMORTGAGE,
    REQUEST_TIMEOUT_SECONDS,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# LTV bands discovered during the validation spike for each purpose.
_LTV_BANDS: dict[str, list[int]] = {
    PURPOSE_REMORTGAGE: [60, 75, 80],
    PURPOSE_FTB: [85, 90, 95, 100],
    PURPOSE_HOME_MOVER: [60, 75, 80, 85, 90, 95],
    PURPOSE_BTL: [60, 75, 80],
}

# URL path templates for each purpose. {ltv} is an integer like 60.
_URL_TEMPLATES: dict[str, str] = {
    PURPOSE_REMORTGAGE: "https://moneyfactscompare.co.uk/mortgages/remortgage/{ltv}-ltv/?sortBy=InitialRate&pageSize=100",
    PURPOSE_FTB: "https://moneyfactscompare.co.uk/mortgages/first-time-buyer-mortgages/{ltv}-ltv/?sortBy=InitialRate&pageSize=100",
    PURPOSE_HOME_MOVER: "https://moneyfactscompare.co.uk/mortgages/{ltv}-ltv-mortgages/?sortBy=InitialRate&pageSize=100",
    PURPOSE_BTL: "https://moneyfactscompare.co.uk/mortgages/buy-to-let/{ltv}-ltv/?sortBy=InitialRate&pageSize=100",
}

_UNFILTERED_URLS: dict[str, str] = {
    PURPOSE_REMORTGAGE: "https://moneyfactscompare.co.uk/mortgages/remortgage/?sortBy=InitialRate&pageSize=100",
    PURPOSE_FTB: "https://moneyfactscompare.co.uk/mortgages/first-time-buyer-mortgages/?sortBy=InitialRate&pageSize=100",
    PURPOSE_HOME_MOVER: "https://moneyfactscompare.co.uk/mortgages/?sortBy=InitialRate&pageSize=100",
    PURPOSE_BTL: "https://moneyfactscompare.co.uk/mortgages/buy-to-let/?sortBy=InitialRate&pageSize=100",
}

# Mobile/desktop browsers accept HTML; keep the request simple and cache-friendly.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-GB,en;q=0.5",
}


def _group_key(rate_type: str | None, term: int | None) -> str:
    """Build a deterministic key for a (rate_type, initial_term) pair.

    The key is used by the coordinator and sensor platform to identify the
    cheapest product for a given product category.
    """
    rate_part = (rate_type or "unknown_type").lower()
    term_part = f"{term}yr" if term is not None else "unknown_term"
    return f"{rate_part}_{term_part}"


class MortgageRatesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that fetches and parses mortgage rates each day.

    The coordinator returns a dictionary keyed by (rate_type, term) groups.
    Each group contains the cheapest product for that category.
    """

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
            always_update=True,
        )
        self._config = dict(config_entry.data)
        self._term = self._config.get(CONF_TERM, 25)
        self._session: aiohttp.ClientSession | None = None

    async def _async_setup(self) -> None:
        """Set up the shared aiohttp session.

        IMPORTANT: Home Assistant's shared client session is used instead of
        creating a new aiohttp.ClientSession. This respects HA connection
        pooling and SSL configuration.
        """
        if self._session is None:
            self._session = async_get_clientsession(self.hass)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the comparison page and return grouped products."""
        await self._async_setup()
        if self._session is None:
            raise UpdateFailed("shared aiohttp session is not available")

        url = self._build_url()
        _LOGGER.debug("Fetching mortgage rates from %s", url)

        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT_SECONDS):
                response = await self._session.get(url, headers=_HEADERS, raise_for_status=True)
                html = await response.text()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"error fetching mortgage rates: {err}") from err
        except asyncio.TimeoutError as err:
            raise UpdateFailed("timeout fetching mortgage rates") from err

        result = self._parse_html(html)

        tracked_raw = self._config.get(CONF_TRACKED_LENDERS, "")
        if tracked_raw:
            lender_names = {l.strip().lower() for l in tracked_raw.split(",") if l.strip()}
            found = {k.split("__")[-1] for k in result if "__" in k}
            missing = lender_names - found
            if missing:
                _LOGGER.info("Lenders not on LTV page, trying unfiltered: %s", missing)
                unfiltered_url = _UNFILTERED_URLS.get(
                    self._config.get(CONF_PURPOSE, PURPOSE_REMORTGAGE)
                )
                try:
                    async with async_timeout.timeout(REQUEST_TIMEOUT_SECONDS):
                        resp = await self._session.get(
                            unfiltered_url, headers=_HEADERS, raise_for_status=True
                        )
                        unfiltered_html = await resp.text()
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    _LOGGER.warning("Failed to fetch unfiltered page for tracked lenders")
                else:
                    unfiltered_products = self._parse_products(unfiltered_html)
                    mortgage_amount = self._config.get(CONF_MORTGAGE_AMOUNT, 0)
                    term_years = self._config.get(CONF_TERM, 25)
                    for lender_name in sorted(missing):
                        matched = [
                            p for p in unfiltered_products
                            if p.get("lender") and lender_name in p["lender"].lower()
                        ]
                        if matched:
                            by_group: dict[str, list[dict]] = {}
                            for p in matched:
                                key = _group_key(p.get("rate_type"), p.get("initial_term_years"))
                                by_group.setdefault(key, []).append(p)
                            for key, prods in by_group.items():
                                best = min(prods, key=lambda p: p["rate"])
                                if mortgage_amount > 0:
                                    best["monthly_payment"] = self._calc_monthly_payment(
                                        best["rate"], mortgage_amount, term_years
                                    )
                                result[f"{key}__{lender_name}"] = best
                            _LOGGER.info("Found '%s' on unfiltered page", lender_name)
                        else:
                            _LOGGER.warning("Lender '%s' not found anywhere", lender_name)

        return result

    def _parse_products(self, html: str) -> list[dict[str, Any]]:
        """Parse HTML and return a flat list of extracted products."""
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("li.mortgages-table-item.table-item")
        products: list[dict[str, Any]] = []
        for row in rows:
            product = self._extract_product(row)
            if product.get("rate") is not None:
                products.append(product)
        return products

    def _build_url(self) -> str:
        """Build the moneyfactscompare URL from configured purpose and LTV."""
        purpose = self._config.get(CONF_PURPOSE, PURPOSE_REMORTGAGE)
        template = _URL_TEMPLATES.get(purpose, _URL_TEMPLATES[PURPOSE_REMORTGAGE])

        property_value = self._config.get(CONF_PROPERTY_VALUE, 0)
        mortgage_amount = self._config.get(CONF_MORTGAGE_AMOUNT, 0)

        ltv = 60
        if property_value and mortgage_amount:
            ltv = int(round((mortgage_amount / property_value) * 100))
            ltv = max(ltv, 1)

        band = self._nearest_ltv_band(purpose, ltv)
        return template.format(ltv=band)

    def _nearest_ltv_band(self, purpose: str, ltv: int) -> int:
        """Return the smallest supported band >= ltv, or the max band if none."""
        bands = _LTV_BANDS.get(purpose, _LTV_BANDS[PURPOSE_REMORTGAGE])
        for band in bands:
            if band >= ltv:
                return band
        return bands[-1]

    def _parse_html(self, html: str) -> dict[str, Any]:
        """Parse the server-rendered HTML and group products by (rate_type, term).

        Returns a dictionary whose keys are ``{rate_type}_{term}yr`` (e.g.
        ``fixed_2yr``, ``variable_2yr``). The special key ``last_updated`` holds
        the UTC timestamp of the update.  Each group value is the cheapest
        product for that group, with the following keys:

        - rate
        - lender
        - monthly_payment
        - aprc
        - product_fees
        - rate_type
        - initial_term_years
        - max_ltv
        """
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("li.mortgages-table-item.table-item")

        if not rows:
            raise UpdateFailed("no rates found")

        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            product = self._extract_product(row)
            if product.get("rate") is not None:
                key = _group_key(product.get("rate_type"), product.get("initial_term_years"))
                groups[key].append(product)

        if not groups:
            raise UpdateFailed("no rates found")

        all_lenders = sorted(set(
            p["lender"] for products in groups.values()
            for p in products if p.get("lender")
        ))
        _LOGGER.info(
            "Parsed %d products across %d groups. Lenders on page: %s",
            sum(len(v) for v in groups.values()), len(groups),
            ", ".join(all_lenders),
        )

        result: dict[str, Any] = {
            key: min(products, key=lambda p: p["rate"])
            for key, products in groups.items()
        }

        # Overwrite the website's default monthly payment with one calculated
        # from the user's configured mortgage amount and term.
        mortgage_amount = self._config.get(CONF_MORTGAGE_AMOUNT, 0)
        term_years = self._config.get(CONF_TERM, 25)
        _LOGGER.info(
            "Calculating monthly payments: mortgage_amount=%s, term=%s years, config=%s",
            mortgage_amount, term_years, self._config,
        )
        for key, product in result.items():
            rate = product.get("rate")
            if rate is not None and mortgage_amount > 0:
                calc = self._calc_monthly_payment(rate, mortgage_amount, term_years)
                _LOGGER.info(
                    "%s: rate=%.2f%% -> scraped=£%s, calculated=£%.2f",
                    key, rate, product.get("monthly_payment"), calc,
                )
                product["monthly_payment"] = calc

        result["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return result

    @staticmethod
    def _calc_monthly_payment(rate: float, mortgage_amount: float, term_years: int) -> float:
        """Calculate monthly repayment using the standard amortisation formula.

        M = P * r * (1+r)^n / ((1+r)^n - 1)

        Where:
            P = mortgage_amount
            r = monthly rate = (rate / 100) / 12
            n = total months = term_years * 12
        """
        r = (rate / 100) / 12
        n = term_years * 12
        if n == 0:
            return 0.0
        if r == 0:
            return round(mortgage_amount / n, 2)
        return round(mortgage_amount * r * (1 + r) ** n / ((1 + r) ** n - 1), 2)

    def _extract_product(self, row: BeautifulSoup) -> dict[str, Any]:
        """Extract a single product's fields from a table row."""
        lender_el = row.select_one(".table-item-heading-product-name strong")
        rate_el = row.select_one(".table-item-cell .table-item-cell-value strong")
        aprc_el = row.select_one(".table-item-cell.aprc .table-item-cell-value strong")
        ltv_el = row.select_one(".table-item-cell.max-ltv-small .table-item-cell-value strong")
        fees_el = row.select_one(".table-item-cell.product-fees .table-item-cell-value strong")
        payment_el = row.select_one(
            ".table-item-cell.initial-monthly-payment .table-item-cell-value strong"
        )
        desc_el = row.select_one(".table-item-cell .table-item-cell-value .small")

        description = desc_el.get_text(strip=True) if desc_el else ""

        lender = lender_el.get_text(strip=True) if lender_el else None
        rate = self._parse_rate(rate_el.get_text(strip=True) if rate_el else "")
        aprc = self._parse_rate(aprc_el.get_text(strip=True) if aprc_el else "")
        max_ltv = self._parse_percentage_int(ltv_el.get_text(strip=True) if ltv_el else "")
        product_fees = self._parse_money(fees_el.get_text(strip=True) if fees_el else "")
        monthly_payment = self._parse_money(payment_el.get_text(strip=True) if payment_el else "")
        rate_type = self._parse_rate_type(description)
        initial_term_years = self._parse_initial_term(description)

        return {
            "lender": lender,
            "rate": rate,
            "aprc": aprc,
            "product_fees": product_fees,
            "monthly_payment": monthly_payment,
            "rate_type": rate_type,
            "initial_term_years": initial_term_years,
            "max_ltv": max_ltv,
        }

    @staticmethod
    def _parse_rate(text: str) -> float | None:
        """Strip '%' and convert to float."""
        text = text.replace("%", "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_percentage_int(text: str) -> int | None:
        """Strip '%' and convert to int."""
        text = text.replace("%", "").strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            return None

    @staticmethod
    def _parse_money(text: str) -> float | None:
        """Strip '£' / commas and convert to float."""
        text = text.replace("£", "").replace(",", "").strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _parse_rate_type(description: str) -> str | None:
        """Extract Fixed / Variable / Tracker from the product description."""
        match = re.search(r"(Fixed|Variable|Tracker)", description, re.IGNORECASE)
        if match:
            return match.group(1).title()
        return None

    @staticmethod
    def _parse_initial_term(description: str) -> int | None:
        """Extract the initial term in years from the product description."""
        match = re.search(r"for\s+(\d+)\s+year", description, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
