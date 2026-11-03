from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.samsungtv_smart.media_player import SamsungTVDevice
from homeassistant.components.media_player import MediaPlayerState


@pytest.fixture
def device(hass):
    entity = object.__new__(SamsungTVDevice)
    entity.hass = hass
    entity.entity_id = "media_player.test_tv"
    entity._state = MediaPlayerState.ON
    entity._external_audio_entity = "media_player.test_receiver"
    entity._external_audio_unsub = None
    entity._attr_volume_level = 0.16
    entity._attr_is_volume_muted = False
    entity._speaker_output_is_internal = MagicMock(return_value=False)
    entity._get_ip_control_client = MagicMock()
    entity._upnp = AsyncMock()
    entity.async_write_ha_state = MagicMock()
    hass.states.async_set(
        entity._external_audio_entity,
        "playing",
        {
            "volume_level": 0.42,
            "is_volume_muted": False,
        },
    )
    entity._attach_external_audio_listener()
    return entity


async def test_actual_receiver_level_replaces_fixed_tv_level_without_reads(device):
    await device._update_volume_info()
    assert device.volume_level == 0.42
    device._get_ip_control_client.assert_not_called()
    device._upnp.async_get_volume.assert_not_awaited()
    device._upnp.async_get_mute.assert_not_awaited()


async def test_remote_volume_and_mute_changes_publish_from_existing_events(
    device, hass
):
    device._attach_external_audio_listener()
    device._apply_external_audio_feedback()
    hass.states.async_set(
        device._external_audio_entity,
        "playing",
        {
            "volume_level": 0.43,
            "is_volume_muted": True,
        },
    )
    await hass.async_block_till_done()
    assert device.volume_level == 0.43
    assert device.is_volume_muted is True
    device.async_write_ha_state.assert_called_once()
    device._external_audio_unsub()


async def test_unrelated_receiver_metadata_does_not_publish_again(device, hass):
    device._attach_external_audio_listener()
    device._apply_external_audio_feedback()
    hass.states.async_set(
        device._external_audio_entity,
        "playing",
        {
            "volume_level": 0.42,
            "is_volume_muted": False,
            "media_title": "New title",
        },
    )
    await hass.async_block_till_done()
    device.async_write_ha_state.assert_not_called()
    device._external_audio_unsub()


@pytest.mark.parametrize("value", [True, "0.4", -1, 1.1, float("nan"), float("inf")])
async def test_invalid_volume_is_unknown_instead_of_a_stale_tv_number(
    device, hass, value
):
    hass.states.async_set(
        device._external_audio_entity,
        "playing",
        {
            "volume_level": value,
            "is_volume_muted": "false",
        },
    )
    await device._update_volume_info()
    assert device.volume_level is None
    assert device.is_volume_muted is None


@pytest.mark.parametrize("state", ["unavailable", "unknown", "off"])
async def test_unavailable_receiver_does_not_serve_old_attributes(device, hass, state):
    hass.states.async_set(
        device._external_audio_entity,
        state,
        {
            "volume_level": 0.42,
            "is_volume_muted": False,
        },
    )
    await device._update_volume_info()
    assert device.volume_level is None
    assert device.is_volume_muted is None


def test_internal_speakers_keep_the_tv_read_path(device):
    device._speaker_output_is_internal.return_value = True
    device._external_audio_feedback_cache = None
    device._attr_volume_level = 0.16
    assert device._apply_external_audio_feedback() is False
    assert device.volume_level == 0.16


def test_optional_link_is_disabled_by_default(device):
    device._external_audio_entity = None
    assert device._apply_external_audio_feedback() is False


def test_self_link_does_not_subscribe_or_reuse_own_feedback(device):
    device._external_audio_entity = device.entity_id
    device._attach_external_audio_listener()
    assert device._external_audio_unsub is None
    assert device._apply_external_audio_feedback() is False


async def test_rebinding_cleans_up_old_listener(device, hass):
    device._attach_external_audio_listener()
    device._external_audio_entity = "media_player.other_receiver"
    device._attach_external_audio_listener()
    hass.states.async_set(
        "media_player.test_receiver",
        "playing",
        {
            "volume_level": 0.7,
            "is_volume_muted": True,
        },
    )
    await hass.async_block_till_done()
    device.async_write_ha_state.assert_not_called()
    device._external_audio_unsub()


async def test_volume_step_does_not_fabricate_receiver_feedback(device):
    device._async_ip_control_volume_step = AsyncMock(return_value=True)
    await device.async_volume_up()
    assert device.volume_level == 0.42
    await device.async_volume_down()
    assert device.volume_level == 0.42
    assert device._async_ip_control_volume_step.await_count == 2


async def test_off_tv_does_not_publish_receiver_events(device, hass):
    device._attach_external_audio_listener()
    device._state = MediaPlayerState.OFF
    hass.states.async_set(
        device._external_audio_entity,
        "playing",
        {
            "volume_level": 0.7,
            "is_volume_muted": True,
        },
    )
    await hass.async_block_till_done()
    device.async_write_ha_state.assert_not_called()
    device._external_audio_unsub()


def test_advanced_options_preserve_explicit_audio_entity(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.samsungtv_smart.config_flow import OptionsFlowHandler
    from custom_components.samsungtv_smart.const import (
        CONF_EXTERNAL_AUDIO_ENTITY,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_EXTERNAL_AUDIO_ENTITY: "media_player.test_receiver",
        },
    )
    flow = OptionsFlowHandler(entry)
    flow.hass = hass
    assert (
        flow._save_entry({})["data"][CONF_EXTERNAL_AUDIO_ENTITY]
        == "media_player.test_receiver"
    )
    flow.async_show_form = MagicMock()
    flow._async_adv_opt_form()
    fields = flow.async_show_form.call_args.kwargs["data_schema"].schema
    marker = next(x for x in fields if x.schema == CONF_EXTERNAL_AUDIO_ENTITY)
    assert marker.description["suggested_value"] == "media_player.test_receiver"
