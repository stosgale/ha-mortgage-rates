# UK Mortgage Rates

A Home Assistant custom integration that fetches the best current UK mortgage rates for your scenario from Moneyfacts. Define your property value, mortgage amount, term, and repayment type via the UI config flow, and the integration surfaces the lowest available rate as a sensor.

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=stosgale&repository=ha-mortgage-rates&category=integration)

## Installation

### HACS (recommended)

1. In HACS, add `https://github.com/stosgale/ha-mortgage-rates` as a custom repository (category: **Integration**).
2. Search for "UK Mortgage Rates" and install it.
3. Restart Home Assistant.

### Manual

Copy the `custom_components/mortgage_rates` directory from this repository into `<config>/custom_components/mortgage_rates/` on your Home Assistant instance, then restart.

## Configuration

The integration is configured entirely through the Home Assistant UI.

1. Go to **Settings** → **Devices & Services** → **Add Integration**.
2. Search for "UK Mortgage Rates" and select it.
3. Fill in the config flow fields:

   | Field | Description |
   |-------|-------------|
   | Property value | The purchase price or estimated value of the property (GBP). |
   | Mortgage amount | How much you need to borrow (GBP). |
   | Purpose | Moving home, remortgaging, or a first-time buyer. |
   | Mortgage term | Loan length in years (e.g. 25). |
   | Repayment type | Capital repayment or interest-only. |

After setup, a set of sensors is created with a title you choose during the flow.

## Entities

| Entity | Type | Description | Attributes |
|--------|------|-------------|------------|
| `sensor.<title>_best_rate` | Sensor (%) | Best current mortgage rate for your scenario | `lender`, `aprc`, `product_fees`, `rate_type`, `initial_term_years`, `max_ltv`, `monthly_payment`, `last_updated` |

## Data source

Rate data is sourced from **moneyfactscompare.co.uk** and refreshed once per day. The data is provided for informational purposes only and does not constitute financial advice.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
