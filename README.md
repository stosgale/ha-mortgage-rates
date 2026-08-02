# UK Mortgage Rates

A Home Assistant custom integration that scrapes the best current UK mortgage rates from [moneyfactscompare.co.uk](https://moneyfactscompare.co.uk) once daily and surfaces the lowest available rate for your scenario as a sensor. Define your property value, mortgage amount, purpose, and term through the UI config flow. Optionally track specific lenders and filter by rate type.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=stosgale&repository=ha-mortgage-rates&category=integration)

## Installation

### HACS (recommended)

1. In HACS, add `https://github.com/stosgale/ha-mortgage-rates` as a custom repository (category: **Integration**).
2. Search for "UK Mortgage Rates" and install it.
3. Restart Home Assistant.

### Manual

Copy the `custom_components/ha_mortgage_rates/` directory from the `stosgale/ha-mortgage-rates` repository into `<config>/custom_components/ha_mortgage_rates/` on your Home Assistant instance, then restart.

## Configuration

The integration is configured entirely through the Home Assistant UI.

1. Go to **Settings** → **Devices & Services** → **Add Integration**.
2. Search for "UK Mortgage Rates" and select it.
3. Fill in the config flow fields:

   | Field | Description |
   |-------|-------------|
   | **Property Value** | The purchase price or estimated value of the property (GBP). |
   | **Mortgage Amount** | How much you need to borrow (GBP). |
   | **Purpose** | One of: Remortgage, First Time Buyer, Home Mover, Buy to Let. |
   | **Term** | Loan length in years (default 25). |
   | **Tracked Lenders** | Comma-separated lender names to track individually (e.g. `Nationwide, HSBC, Barclays`). Leave blank for best-rate only. Matching is case-insensitive substring. |
   | **Rate Types** | Comma-separated rate types to include (e.g. `variable, fixed_2yr, fixed_5yr`). Leave blank for all types. Matches by group key prefix. |

   The loan-to-value (LTV) ratio is computed automatically from your Property Value and Mortgage Amount.

After setup, sensors are created for each `(rate_type, term)` combination found on the page (e.g. `fixed_2yr`, `variable_2yr`, `fixed_5yr`). If tracked lenders are configured, additional sensors are created per lender per group. Add multiple config entries to compare different scenarios (e.g. remortgage vs new purchase).

### Reconfiguring

After initial setup, you can change any value — including tracked lenders and rate types — without deleting the entry. Go to **Settings → Devices & Services → UK Mortgage Rates → ⋮ → Reconfigure**.

## Entities

For each `(rate_type, term)` group returned by Moneyfacts, the following sensors are created:

### Cheapest overall

| Entity suffix | Type | Description |
|---------------|------|-------------|
| `_rate` | Sensor (%) | Cheapest initial rate for the group |
| `_lender` | Sensor | Lender offering the cheapest product |
| `_monthly_payment` | Sensor (GBP) | Monthly payment for the cheapest product |

### Tracked lenders (per configured lender)

| Entity suffix | Type | Description |
|---------------|------|-------------|
| `__<lender>_rate` | Sensor (%) | The lender's best rate in that group |
| `__<lender>_monthly_payment` | Sensor (GBP) | Monthly payment for that product |

Each sensor carries the full product data (APRC, fees, LTV, etc.) as attributes plus a `last_updated` timestamp.

Examples:
- `sensor.my_mortgage_fixed_2yr_rate` — cheapest fixed 2yr rate
- `sensor.my_mortgage_fixed_2yr__hsbc_rate` — HSBC's best fixed 2yr rate
- `sensor.my_mortgage_variable_2yr__nationwide_monthly_payment` — Nationwide's variable 2yr payment

## Data source

Rate data is sourced from **moneyfactscompare.co.uk** and refreshed once per day. The data is provided for informational purposes only and does not constitute financial advice.

## Limitations

- **Best rate = lowest initial rate per group.** Products are ranked by their initial interest rate within each `(rate_type, term)` group, not by total cost over the term.
- **No built-in alerts.** Use Home Assistant automations to send alerts when a rate drops below a threshold.
- **Lender matching is case-insensitive substring.** `Nationwide` matches `Nationwide BS`. Use exact names from the site for best results.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
