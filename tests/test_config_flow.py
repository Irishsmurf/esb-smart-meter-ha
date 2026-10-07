from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.esb_smart_meter.api import ESBAuthError, ESBCaptchaError, ESBError
from custom_components.esb_smart_meter.const import CONF_UPDATE_INTERVAL, DOMAIN

from .conftest import MPRN

LOGIN = "custom_components.esb_smart_meter.api.ESBNetworksAPI.login"
USER_INPUT = {"username": "me@example.com", "password": "pw", "mprn": "100 1234 5678"}


@pytest.fixture(autouse=True)
def _custom(recorder_mock, enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.esb_smart_meter.async_setup_entry", return_value=True):
        yield


async def test_user_flow_checks_login_and_cleans_mprn(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(LOGIN) as login:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert login.call_count == 1
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["mprn"] == MPRN
    assert result["result"].unique_id == MPRN


@pytest.mark.parametrize(
    ("error", "key"),
    [(ESBAuthError("x"), "invalid_auth"), (ESBCaptchaError("x"), "rate_limited"), (ESBError("x"), "cannot_connect")],
)
async def test_user_flow_login_errors(hass, error, key):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(LOGIN, side_effect=error):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}


async def test_user_flow_rejects_bad_mprn_without_logging_in(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(LOGIN) as login:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {**USER_INPUT, "mprn": "123"})
    assert result["errors"] == {"mprn": "invalid_mprn"}
    assert login.call_count == 0


async def test_reauth_updates_credentials(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MPRN, data={"username": "me@example.com", "password": "old", "mprn": MPRN})
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(LOGIN):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"username": "me@example.com", "password": "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"


async def test_options_flow_sets_interval(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MPRN, data={"username": "u", "password": "p", "mprn": MPRN})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_UPDATE_INTERVAL: 12})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_UPDATE_INTERVAL: 12}
