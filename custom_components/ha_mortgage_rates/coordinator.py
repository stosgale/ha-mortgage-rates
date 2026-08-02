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
import json
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

        ltv_band = self._get_ltv_band()
        try:
            products = self._parse_products_from_js(html, ltv_band)
        except Exception:
            _LOGGER.exception("JS parse failed, falling back to HTML parser")
            products = []
        if not products:
            _LOGGER.warning("JS parse returned no products, falling back to HTML parser")
            result = self._parse_html(html)
            products = self._parse_products_from_html(html, ltv_band)
        else:
            result = self._build_result(products)

        tracked_raw = self._config.get(CONF_TRACKED_LENDERS, "")
        if tracked_raw:
            lender_names = {l.strip().lower() for l in tracked_raw.split(",") if l.strip()}
            mortgage_amount = self._config.get(CONF_MORTGAGE_AMOUNT, 0)
            term_years = self._config.get(CONF_TERM, 25)
            missing = self._add_tracked_lenders(
                result, products, lender_names, mortgage_amount, term_years
            )
            if missing:
                purpose = self._config.get(CONF_PURPOSE, PURPOSE_REMORTGAGE)
                current_ltv = int(
                    round(mortgage_amount / self._config.get(CONF_PROPERTY_VALUE, 1) * 100)
                ) if self._config.get(CONF_PROPERTY_VALUE) else None
                for band in _LTV_BANDS.get(purpose, []):
                    if current_ltv is not None and band == self._nearest_ltv_band(purpose, current_ltv):
                        continue
                    url = _URL_TEMPLATES.get(purpose, _URL_TEMPLATES[PURPOSE_REMORTGAGE]).format(ltv=band)
                    try:
                        async with async_timeout.timeout(REQUEST_TIMEOUT_SECONDS):
                            resp = await self._session.get(url, headers=_HEADERS, raise_for_status=True)
                            band_html = await resp.text()
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        continue
                    band_products = []
                    try:
                        band_products = self._parse_products_from_js(band_html, band)
                    except Exception:
                        _LOGGER.exception("JS parse failed for LTV band %d", band)
                    if not band_products:
                        band_products = self._parse_products_from_html(band_html, band)
                    still_missing = self._add_tracked_lenders(
                        result, band_products, missing, mortgage_amount, term_years
                    )
                    if not still_missing:
                        break
                    missing = still_missing
                if missing:
                    for name in sorted(missing):
                        _LOGGER.warning("Lender '%s' not found in any LTV band", name)

        return result

    def _get_ltv_band(self) -> int:
        """Return the LTV band for the current page URL."""
        property_value = self._config.get(CONF_PROPERTY_VALUE, 0)
        mortgage_amount = self._config.get(CONF_MORTGAGE_AMOUNT, 0)
        if property_value and mortgage_amount:
            ltv = int(round((mortgage_amount / property_value) * 100))
            ltv = max(ltv, 1)
        else:
            ltv = 60
        purpose = self._config.get(CONF_PURPOSE, PURPOSE_REMORTGAGE)
        return self._nearest_ltv_band(purpose, ltv)

    def _parse_products_from_js(self, html: str, ltv_band: int) -> list[dict[str, Any]]:
        """Parse all products from the embedded JavaScript Results array."""
        m = re.search(r'"Results"\s*:\s*\[(.*)', html, re.DOTALL)
        if not m:
            return []
        rest = m.group(1)
        depth = 1
        end = 0
        for i, c in enumerate(rest):
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        results_str = rest[:end]
        products: list[dict[str, Any]] = []
        for ap in re.finditer(r'"AllProducts"\s*:\s*\[({[^]]+})\]', results_str, re.DOTALL):
            try:
                raw = json.loads(ap.group(1))
            except json.JSONDecodeError:
                continue
            rate = raw.get("Rate")
            if rate is None:
                continue
            description = raw.get("Description", "")
            products.append({
                "lender": raw.get("Company"),
                "rate": float(rate),
                "aprc": float(raw.get("APRC", 0)),
                "product_fees": float(raw.get("ProductFees", 0)),
                "monthly_payment": float(raw.get("InitialMonthlyPayment", 0)),
                "rate_type": MortgageRatesCoordinator._parse_rate_type(description),
                "initial_term_years": MortgageRatesCoordinator._parse_initial_term(description),
                "max_ltv": ltv_band,
            })
        _LOGGER.info(
            "JS parse: %d products from %d AllProducts entries",
            len(products),
            len(list(re.finditer(r'"AllProducts"\s*:\s*\[({[^]]+})\]', results_str, re.DOTALL))),
        )
        return products

    def _parse_products_from_html(self, html: str, ltv_band: int) -> list[dict[str, Any]]:
        """Fallback: parse products from visible HTML cards."""
        soup = BeautifulSoup(html, "html.parser")
        products: list[dict[str, Any]] = []
        for row in soup.select("li.mortgages-table-item.table-item"):
            product = self._extract_product(row)
            if product.get("rate") is not None:
                products.append(product)
        return products

    def _build_result(self, products: list[dict[str, Any]]) -> dict[str, Any]:
        """Group products by (rate_type, term), find cheapest, calculate payments."""
        groups: dict[str, list[dict]] = {}
        for p in products:
            key = _group_key(p.get("rate_type"), p.get("initial_term_years"))
            groups.setdefault(key, []).append(p)
        if not groups:
            raise UpdateFailed("no rates found")
        result = {key: min(prods, key=lambda p: p["rate"]) for key, prods in groups.items()}
        mortgage_amount = self._config.get(CONF_MORTGAGE_AMOUNT, 0)
        term_years = self._config.get(CONF_TERM, 25)
        if mortgage_amount > 0:
            for key, product in result.items():
                product["monthly_payment"] = self._calc_monthly_payment(
                    product["rate"], mortgage_amount, term_years
                )
        result["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return result

    def _add_tracked_lenders(
        self,
        result: dict[str, Any],
        products: list[dict[str, Any]],
        lender_names: set[str],
        mortgage_amount: float,
        term_years: int,
    ) -> set[str]:
        """Add tracked lenders found in products to result. Returns still-missing names."""
        still_missing: set[str] = set()
        by_lender: dict[str, list[dict]] = {}
        for p in products:
            lender = (p.get("lender") or "").lower()
            for name in lender_names:
                if name in lender:
                    by_lender.setdefault(name, []).append(p)

        for name in sorted(lender_names):
            matched = by_lender.get(name, [])
            if not matched:
                still_missing.add(name)
                continue
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
                result[f"{key}__{name}"] = best
            _LOGGER.info("Tracked lender '%s': %d products across groups", name, len(matched))
        return still_missing

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
