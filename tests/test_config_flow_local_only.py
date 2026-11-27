"""First setup offers "Local only" and no longer looks as if a PAT were required.

The first screen listed SmartThings methods only (OAuth, PAT, the SmartThings
integration), so a new user believed a SmartThings token was required, though
the PAT field was optional and the integration runs on its local channels.
SmartThings' API now needs a paid plan. "Local only" existed on reconfigure
alone (#258); it is now the first choice, and the default unless OAuth is set
up. A blank PAT form is stored as local only too.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.samsungtv_smart import config_flow
from custom_components.samsungtv_smart.const import (
    AUTH_METHOD_NONE,
    AUTH_METHOD_PAT,
    CONF_AUTH_METHOD,
    DOMAIN,
    RESULT_SUCCESS,
)
from homeassistant import config_entries, data_entry_flow
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_NAME

FLOW = config_flow.SamsungTVConfigFlow


@pytest.fixture
def tv(monkeypatch):
    """A reachable TV: no network, no SmartThings call."""
    state = {"result": RESULT_SUCCESS, "api_keys": []}

    async def fake_try_connect(self, *, port=None, token=None, skip_info=False):
        state["api_keys"].append(self._api_key)
        self._tv_info = SimpleNamespace(ws_port=8002, ping_port=0)
        self._token = "ws-token"
        self._ping_port = 0
        self._device_info = {"device_mac": "aa:bb:cc:dd:ee:ff"}
        return state["result"]

    async def no_st(self):
        raise AssertionError("a local-only setup must not look for SmartThings")

    monkeypatch.setattr(FLOW, "_try_connect", fake_try_connect)
    monkeypatch.setattr(FLOW, "_get_st_deviceid", no_st)
    monkeypatch.setattr(config_flow, "_get_ip", lambda host: "192.168.1.50")
    # Creating the entry would set the integration up for real (network).
    with (
        patch.object(FLOW, "_async_oauth_available", return_value=False),
        patch("custom_components.samsungtv_smart.async_setup", return_value=True),
        patch("custom_components.samsungtv_smart.async_setup_entry", return_value=True),
    ):
        yield state


def _choices(result):
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == "auth_method")
    options = schema[key].config["options"]
    return [o["value"] for o in options], key.default()


async def test_the_first_screen_offers_local_only_first_and_by_default(hass, tv):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    values, default = _choices(result)
    assert values[0] == AUTH_METHOD_NONE
    assert default == AUTH_METHOD_NONE
    assert AUTH_METHOD_PAT in values  # still available


async def test_oauth_stays_the_default_when_it_is_set_up(hass, tv):
    with patch.object(FLOW, "_async_oauth_available", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    values, default = _choices(result)
    assert default == "oauth"
    assert values[0] == AUTH_METHOD_NONE


async def test_local_only_creates_an_entry_without_any_smartthings_key(hass, tv):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"auth_method": AUTH_METHOD_NONE}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "local"
    fields = {str(k) for k in result["data_schema"].schema}
    assert fields == {CONF_HOST, CONF_NAME, "use_ha_name_for_ws"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "tv.local", CONF_NAME: "Salon"}
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["title"] == "Salon"
    data = result["data"]
    assert data[CONF_AUTH_METHOD] == AUTH_METHOD_NONE
    assert CONF_API_KEY not in data
    assert data[CONF_HOST] == "192.168.1.50"
    assert tv["api_keys"] == [None]


async def test_a_failed_connection_shows_the_local_form_again(hass, tv):
    tv["result"] = "not_successful"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"auth_method": AUTH_METHOD_NONE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "tv.local", CONF_NAME: "Salon"}
    )
    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "local"
    assert result["errors"] == {"base": "not_successful"}


async def test_a_blank_pat_form_is_stored_as_local_only(hass, tv):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"auth_method": AUTH_METHOD_PAT}
    )
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "tv.local", CONF_NAME: "Chambre"}
    )
    assert result["type"] is data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AUTH_METHOD] == AUTH_METHOD_NONE
    assert CONF_API_KEY not in result["data"]


async def test_a_failed_pat_form_shows_the_pat_form_again(hass, tv):
    tv["result"] = "not_successful"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"auth_method": AUTH_METHOD_PAT}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "tv.local", CONF_NAME: "Chambre"}
    )
    assert result["step_id"] == "manual"


def test_every_translation_with_the_user_step_has_the_local_step():
    import glob
    import json
    import os

    base = os.path.dirname(config_flow.__file__)
    for path in [os.path.join(base, "strings.json")] + glob.glob(
        os.path.join(base, "translations", "*.json")
    ):
        with open(path, encoding="utf-8") as f:
            steps = json.load(f).get("config", {}).get("step", {})
        if "user" not in steps:
            continue
        assert "local" in steps, path
        assert set(steps["local"]["data"]) == {
            CONF_HOST,
            CONF_NAME,
            "use_ha_name_for_ws",
        }
        assert "{oauth_status}" in steps["user"]["description"], path
