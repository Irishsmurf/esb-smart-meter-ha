import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import ESBAuthError, ESBCaptchaError, ESBError, ESBLoginBackoff, ESBNetworksAPI
from .const import (
    CONF_MPRN,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    DOMAIN,
    MAX_UPDATE_INTERVAL_HOURS,
    MIN_UPDATE_INTERVAL_HOURS,
    NAME,
)
from .coordinator import clean_mprn, session_cache_path

_LOGGER = logging.getLogger(DOMAIN)


class ESBSmartMeterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ESBOptionsFlow()

    async def _async_check_login(self, username: str, password: str, mprn: str) -> str | None:
        """Log in once to validate the credentials; returns an error key or None.

        The resulting session is cached, so the first data fetch reuses it rather
        than spending another of ESB's few allowed logins.
        """
        api = ESBNetworksAPI(username, password, mprn, session_cache_path(self.hass, mprn))
        try:
            await self.hass.async_add_executor_job(api.login)
        except ESBAuthError:
            return "invalid_auth"
        except (ESBCaptchaError, ESBLoginBackoff):
            return "rate_limited"
        except ESBError as e:
            _LOGGER.warning("ESB: login check failed: %s", e)
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            mprn = clean_mprn(user_input[CONF_MPRN])
            if not re.fullmatch(r"\d{11}", mprn):
                errors[CONF_MPRN] = "invalid_mprn"
            else:
                await self.async_set_unique_id(mprn)
                self._abort_if_unique_id_configured()
                error = await self._async_check_login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD], mprn)
                if error:
                    errors["base"] = error
                else:
                    return self.async_create_entry(title=NAME, data={**user_input, CONF_MPRN: mprn})

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema({
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                    vol.Required(CONF_MPRN): str,
                }),
                user_input,
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            error = await self._async_check_login(
                user_input[CONF_USERNAME], user_input[CONF_PASSWORD], entry.data[CONF_MPRN]
            )
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME, default=entry.data[CONF_USERNAME]): str,
                vol.Required(CONF_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }),
            errors=errors,
        )


class ESBOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL])})

        current = self.config_entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL_HOURS)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_UPDATE_INTERVAL, default=current): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_UPDATE_INTERVAL_HOURS,
                        max=MAX_UPDATE_INTERVAL_HOURS,
                        step=1,
                        unit_of_measurement="h",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
            }),
        )
