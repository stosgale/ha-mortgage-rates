"""Config flow for UK Mortgage Rates integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_MORTGAGE_AMOUNT,
    CONF_PROPERTY_VALUE,
    CONF_PURPOSE,
    CONF_TERM,
    CONF_TRACKED_LENDERS,
    DEFAULT_TERM,
    DEFAULT_TRACKED_LENDERS,
    DOMAIN,
    PURPOSE_BTL,
    PURPOSE_FTB,
    PURPOSE_HOME_MOVER,
    PURPOSE_REMORTGAGE,
    PURPOSES,
)

PURPOSE_LABELS = {
    PURPOSE_REMORTGAGE: "Remortgage",
    PURPOSE_FTB: "First Time Buyer",
    PURPOSE_HOME_MOVER: "Home Mover",
    PURPOSE_BTL: "Buy to Let",
}

_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PROPERTY_VALUE): vol.Coerce(int),
        vol.Required(CONF_MORTGAGE_AMOUNT): vol.Coerce(int),
        vol.Required(CONF_PURPOSE): vol.In(PURPOSE_LABELS),
        vol.Optional(CONF_TERM, default=DEFAULT_TERM): vol.Coerce(int),
        vol.Optional(
            CONF_TRACKED_LENDERS, default=DEFAULT_TRACKED_LENDERS
        ): str,
    }
)


class MortgageRatesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for UK Mortgage Rates."""

    VERSION = 1
    MINOR_VERSION = 1

    @staticmethod
    def _validate(user_input: dict) -> dict[str, str]:
        """Validate the user input. Returns a dict of field -> error key."""
        errors: dict[str, str] = {}
        property_value = user_input[CONF_PROPERTY_VALUE]
        mortgage_amount = user_input[CONF_MORTGAGE_AMOUNT]
        term = user_input.get(CONF_TERM, DEFAULT_TERM)

        if property_value <= 0:
            errors[CONF_PROPERTY_VALUE] = "invalid_value"
        elif mortgage_amount <= 0:
            errors[CONF_MORTGAGE_AMOUNT] = "invalid_value"
        elif mortgage_amount >= property_value:
            errors[CONF_MORTGAGE_AMOUNT] = "invalid_amount"
        elif term < 1 or term > 40:
            errors[CONF_TERM] = "invalid_value"
        return errors

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                property_value = user_input[CONF_PROPERTY_VALUE]
                mortgage_amount = user_input[CONF_MORTGAGE_AMOUNT]
                purpose = user_input[CONF_PURPOSE]
                term = user_input.get(CONF_TERM, DEFAULT_TERM)
                ltv = int(mortgage_amount / property_value * 100)
                unique_id = f"{purpose}_{ltv}_{term or DEFAULT_TERM}"

                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                purpose_label = PURPOSE_LABELS.get(purpose, purpose)
                title = f"{purpose_label} ({ltv}% LTV, {term}yr)"

                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        current_data = dict(entry.data)

        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                property_value = user_input[CONF_PROPERTY_VALUE]
                mortgage_amount = user_input[CONF_MORTGAGE_AMOUNT]
                purpose = user_input[CONF_PURPOSE]
                term = user_input.get(CONF_TERM, DEFAULT_TERM)
                ltv = int(mortgage_amount / property_value * 100)

                await self.async_set_unique_id(
                    f"{purpose}_{ltv}_{term or DEFAULT_TERM}"
                )
                self._abort_if_unique_id_mismatch()

                purpose_label = PURPOSE_LABELS.get(purpose, purpose)
                title = f"{purpose_label} ({ltv}% LTV, {term}yr)"

                self.hass.config_entries.async_update_entry(
                    entry, data={**current_data, **user_input}, title=title
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROPERTY_VALUE,
                        default=current_data.get(CONF_PROPERTY_VALUE),
                    ): vol.Coerce(int),
                    vol.Required(
                        CONF_MORTGAGE_AMOUNT,
                        default=current_data.get(CONF_MORTGAGE_AMOUNT),
                    ): vol.Coerce(int),
                    vol.Required(
                        CONF_PURPOSE,
                        default=current_data.get(CONF_PURPOSE),
                    ): vol.In(PURPOSE_LABELS),
                    vol.Optional(
                        CONF_TERM,
                        default=current_data.get(CONF_TERM, DEFAULT_TERM),
                    ): vol.Coerce(int),
                    vol.Optional(
                        CONF_TRACKED_LENDERS,
                        default=current_data.get(
                            CONF_TRACKED_LENDERS, DEFAULT_TRACKED_LENDERS
                        ),
                    ): str,
                }
            ),
            errors=errors,
        )
