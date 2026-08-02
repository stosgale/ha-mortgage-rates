# UK Mortgage Rates

A Home Assistant custom integration that scrapes the best current UK mortgage rates from [moneyfactscompare.co.uk](https://moneyfactscompare.co.uk) once daily and surfaces the lowest available rate for your scenario as a sensor. Define your property value, mortgage amount, purpose, and term through the UI config flow, and the integration handles the rest.

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

After setup, a set of sensors is created with a title you choose during the flow. The coordinator groups products by `(rate_type, initial_term_years)` and creates one sensor family per group (e.g. `fixed_2yr`, `variable_2yr`, `fixed_5yr`). You can add multiple config entries to track different scenarios (e.g. compare a remortgage against a new purchase).

### Reconfiguring

After initial setup, you can change any value — including tracked lenders and rate types — without deleting the entry. Go to **Settings → Devices & Services → UK Mortgage Rates → ⋮ → Reconfigure**.

## Entities

For each `(rate_type, term)` group returned by Moneyfacts, three sensors are created:

| Entity suffix | Type | Description | Attributes |
|---------------|------|-------------|------------|
| `_rate` | Sensor (%) | Cheapest initial rate for the group | full group product data + `last_updated` |
| `_lender` | Sensor | Lender offering the cheapest product in the group | full group product data + `last_updated` |
| `_monthly_payment` | Sensor (GBP) | Initial monthly payment for the cheapest product in the group | full group product data + `last_updated` |

Sensor names follow the pattern `sensor.<title>_<type>_<term>yr_<field>`, e.g.
`sensor.my_mortgage_fixed_2yr_rate`, `sensor.my_mortgage_variable_2yr_lender`.

## Data source

Rate data is sourced from **moneyfactscompare.co.uk** and refreshed once per day. The data is provided for informational purposes only and does not constitute financial advice.

## Limitations

- **Best rate = lowest initial rate per group.** Products are ranked by their initial interest rate within each `(rate_type, term)` group, not by total cost over the term. A product with a lower rate but higher fees may rank above a product with a higher rate but lower overall cost.
- **No built-in alerts.** There is no native notification system for rate changes. Use Home Assistant automations to send alerts when a rate drops below a threshold or a lender/product changes.
- **Data quality depends on Moneyfacts.** All rate and product information is provided as-is from moneyfactscompare.co.uk. Verify details directly with lenders before making any financial decision.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
