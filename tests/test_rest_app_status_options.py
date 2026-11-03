from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsungtv_smart.config_flow import (
    ADVANCED_OPTIONS,
    OptionsFlowHandler,
    _validate_rest_app_list,
)
from custom_components.samsungtv_smart.const import (
    CONF_REST_APP_LIST,
    CONF_REST_APP_POLL_INTERVAL,
    DEFAULT_REST_APP_LIST,
    DEFAULT_REST_APP_POLL_INTERVAL,
    DOMAIN,
)
from custom_components.samsungtv_smart.media_player import SamsungTVDevice


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"Player": 1},
        {1: "com.example.player"},
        {" ": "com.example.player"},
        {"Player": ""},
        {"Player": "../other"},
        {"Player": "app?query=1"},
        {"Player": "app/another"},
        {"Player": "app#fragment"},
        {"Player": "app name"},
        {"Player": "com.example.player", "Other": "com.example.player"},
        {"Player": "com.example.player", " Player ": "com.example.other"},
    ],
)
def test_invalid_mappings_are_rejected(value):
    assert _validate_rest_app_list(value) is None


def test_valid_mapping_is_normalized_and_empty_disables_checks():
    assert _validate_rest_app_list({}) == {}
    assert _validate_rest_app_list({" Player ": " com.example.Player "}) == {
        "Player": "com.example.Player"
    }


@pytest.fixture
def flow(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_REST_APP_LIST: {"Player": "com.example.player"},
            CONF_REST_APP_POLL_INTERVAL: 60,
        },
    )
    options_flow = OptionsFlowHandler(entry)
    options_flow.hass = hass
    return options_flow


async def test_invalid_form_retains_saved_options_and_reports_the_field(flow):
    before = dict(flow._adv_options)
    result = await flow.async_step_adv_opt(
        {CONF_REST_APP_LIST: {"Player": "../bad"}, CONF_REST_APP_POLL_INTERVAL: 5}
    )
    assert result["errors"] == {CONF_REST_APP_LIST: "invalid_rest_app_list"}
    assert flow._adv_options == before


async def test_empty_form_mapping_is_saved_as_an_explicit_opt_out(flow):
    flow.async_step_menu = AsyncMock(return_value={"type": "menu"})
    await flow.async_step_adv_opt(
        {CONF_REST_APP_LIST: {}, CONF_REST_APP_POLL_INTERVAL: 5}
    )
    assert flow._adv_options[CONF_REST_APP_LIST] == {}
    assert flow._adv_options[CONF_REST_APP_POLL_INTERVAL] == 5


def test_saved_options_keep_both_new_settings(flow):
    assert CONF_REST_APP_LIST in ADVANCED_OPTIONS
    assert CONF_REST_APP_POLL_INTERVAL in ADVANCED_OPTIONS
    result = flow._save_entry({})
    assert result["data"][CONF_REST_APP_LIST] == {"Player": "com.example.player"}
    assert result["data"][CONF_REST_APP_POLL_INTERVAL] == 60


def test_form_uses_distribution_defaults_when_no_values_are_saved(flow):
    flow._adv_options = {}
    flow.async_show_form = Mock()
    flow._async_adv_opt_form()
    fields = flow.async_show_form.call_args.kwargs["data_schema"].schema
    marker = next(key for key in fields if key.schema == CONF_REST_APP_LIST)
    assert marker.description["suggested_value"] == DEFAULT_REST_APP_LIST
    marker = next(key for key in fields if key.schema == CONF_REST_APP_POLL_INTERVAL)
    assert marker.default() == DEFAULT_REST_APP_POLL_INTERVAL


@pytest.mark.parametrize("explicit", [False, True])
def test_runtime_options_honor_empty_maps_and_explicit_intervals(explicit):
    device = object.__new__(SamsungTVDevice)
    options = (
        {CONF_REST_APP_LIST: {}, CONF_REST_APP_POLL_INTERVAL: 5} if explicit else {}
    )
    device._get_option = lambda key, default=None: options.get(key, default)
    device._load_tv_lists = Mock()
    device._ws = Mock()
    device._app_list = {}

    device._update_config_options()

    assert device._rest_app_list == ({} if explicit else DEFAULT_REST_APP_LIST)
    assert device._rest_app_poll_interval == (
        5 if explicit else DEFAULT_REST_APP_POLL_INTERVAL
    )


@pytest.mark.parametrize("interval", [0, 4, 3601])
def test_app_interval_rejects_out_of_range_values(flow, interval):
    import voluptuous as vol

    flow.async_show_form = Mock()
    flow._async_adv_opt_form()
    fields = flow.async_show_form.call_args.kwargs["data_schema"].schema
    marker = next(key for key in fields if key.schema == CONF_REST_APP_POLL_INTERVAL)
    with pytest.raises(vol.Invalid):
        fields[marker](interval)
