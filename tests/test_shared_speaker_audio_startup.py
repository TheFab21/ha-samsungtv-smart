import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.samsungtv_smart.media_player import SamsungTVDevice

from .test_external_audio_feedback import device


def bind_shared(entity, value="external", age=0):
    del entity._speaker_output_is_internal
    entity._speaker_output_state = MagicMock(return_value=None)
    entity._external_audio_ip_entry = lambda: SimpleNamespace(options={})
    coordinator = SimpleNamespace(
        last_update_success=True,
        data={"tv": {"speakerSelect": value}, "polled_at": time.monotonic() - age},
    )
    entity._get_ip_control_state_coordinator = lambda: coordinator
    return coordinator


async def test_fresh_shared_external_output_works_before_selector_discovery(device):
    bind_shared(device)
    await device._update_volume_info()
    assert device.volume_level == 0.42
    device._speaker_output_state.assert_not_called()
    device._get_ip_control_client.assert_not_called()
    device._upnp.async_get_volume.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "expected"),
    [("internal", True), ("External", False), ("AudioOut/Optical", False)],
)
def test_canonical_shared_output_is_used_directly(device, value, expected):
    bind_shared(device, value)
    assert device._speaker_output_is_internal() is expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        0,
        {},
        "",
        "unknown",
        "unavailable",
        "null",
        "unrecognized-device-id",
    ],
)
def test_invalid_or_unresolved_shared_values_keep_the_selector_fallback(device, value):
    bind_shared(device, value)
    device._speaker_output_state.return_value = "Internal"
    assert device._speaker_output_is_internal() is True
    device._speaker_output_state.assert_called_once()


@pytest.mark.parametrize(
    "problem", ["stale", "future", "failed", "powered_off", "disabled"]
)
def test_unusable_snapshot_does_not_override_the_output_selector(device, problem):
    coordinator = bind_shared(device)
    if problem == "stale":
        coordinator.data["polled_at"] -= 100
    if problem == "future":
        coordinator.data["polled_at"] += 100
    if problem == "failed":
        coordinator.last_update_success = False
    if problem == "powered_off":
        coordinator.data["powered_off"] = True
    if problem == "disabled":
        device._external_audio_ip_entry = lambda: None
    device._speaker_output_state.return_value = "Internal"
    assert device._speaker_output_is_internal() is True


async def test_unknown_output_shows_unknown_audio_without_querying_tv_volume(device):
    bind_shared(device, None)
    device._attr_volume_level = 0.0
    await device._update_volume_info()
    assert device.volume_level is None
    assert device.is_volume_muted is None
    device._upnp.async_get_volume.assert_not_awaited()
    device._get_ip_control_client.assert_not_called()


def test_shared_output_event_applies_feedback_before_one_publication(device):
    bind_shared(device)
    device._attr_volume_level = 0.0
    device._external_audio_feedback_cache = (None, None)
    device._external_audio_ip_updated()
    assert device.volume_level == 0.42
    device.async_write_ha_state.assert_called_once()


async def test_art_exit_callback_does_not_publish_an_assumed_zero(device, hass):
    bind_shared(device)
    device._attr_volume_level = 0.0
    device._st = None
    device._refresh_ip_art_mode = AsyncMock()
    published = []
    device.async_write_ha_state.side_effect = lambda: published.append(
        device.volume_level
    )
    device._on_art_transition()
    await hass.async_block_till_done()
    assert published == [0.42]


def test_initial_link_configuration_clears_the_legacy_zero_default(device):
    device._external_audio_entity = None
    device._attr_volume_level = 0.0
    device._speaker_output_is_internal.return_value = None
    device._get_option = lambda name, default=None: (
        "media_player.test_receiver" if name == "external_audio_entity" else default
    )
    device._load_tv_lists = MagicMock()
    device._app_list = {}
    device._ws = MagicMock()
    device._update_config_options(first_load=True)
    assert device.volume_level is None
    assert device.is_volume_muted is None


def test_public_properties_are_correct_before_any_audio_update_runs(device):
    bind_shared(device)
    device._attr_volume_level = 0.0
    device._attr_is_volume_muted = True
    assert device.volume_level == 0.42
    assert device.is_volume_muted is False


@pytest.mark.parametrize("output", ["internal", "external"])
def test_ambient_snapshot_cannot_supply_a_normal_viewing_output(device, output):
    coordinator = bind_shared(device, output)
    coordinator.data["tv"]["pictureMode"] = "Ambient"
    device._attr_volume_level = 0.0
    device._external_audio_ip_updated()
    assert device._speaker_output_is_internal() is None
    assert device.volume_level is None
    assert device.is_volume_muted is None


async def test_a_real_receiver_zero_is_still_reported(device, hass):
    bind_shared(device)
    hass.states.async_set(
        device._external_audio_entity,
        "playing",
        {
            "volume_level": 0.0,
            "is_volume_muted": False,
        },
    )
    await hass.async_block_till_done()
    assert device.volume_level == 0.0


def test_known_internal_speakers_keep_their_actual_zero(device):
    bind_shared(device, "internal")
    device._external_audio_feedback_cache = None
    device._attr_volume_level = 0.0
    assert device.volume_level == 0.0


async def test_repeated_mute_request_keeps_the_existing_toggle_guard(device):
    device.async_send_command = AsyncMock(return_value=True)
    await device.async_mute_volume(True)
    await device.async_mute_volume(True)
    device.async_send_command.assert_awaited_once_with("KEY_MUTE")


async def test_immediate_unmute_is_not_lost_while_receiver_feedback_catches_up(device):
    device.async_send_command = AsyncMock(return_value=True)
    await device.async_mute_volume(True)
    await device.async_mute_volume(False)
    assert device.async_send_command.await_count == 2
    assert device._attr_is_volume_muted is False


def test_shared_listener_binds_when_status_becomes_available(device):
    device._external_audio_feedback_cache = (None, None)
    coordinator = bind_shared(device)
    unsubscribe = MagicMock()
    coordinator.async_add_listener = MagicMock(return_value=unsubscribe)
    device._get_ip_control_state_coordinator = lambda: None
    device._attach_external_audio_ip_listener()
    device.async_write_ha_state.assert_not_called()

    device._get_ip_control_state_coordinator = lambda: coordinator
    device._attach_external_audio_ip_listener()
    coordinator.async_add_listener.assert_called_once_with(
        device._external_audio_ip_updated
    )
    device.async_write_ha_state.assert_called_once()
    device._get_ip_control_client.assert_not_called()

    device._attach_external_audio_ip_listener()
    coordinator.async_add_listener.assert_called_once()
    device._external_audio_entity = None
    device._attach_external_audio_ip_listener()
    unsubscribe.assert_called_once()
    assert device._external_audio_ip_unsub is None


def test_unchanged_snapshot_does_not_republish_audio(device, hass):
    bind_shared(device)
    hass.states.async_set(
        device.entity_id,
        "on",
        {"volume_level": 0.42, "is_volume_muted": False},
    )
    device._external_audio_ip_updated()
    device.async_write_ha_state.assert_not_called()


async def test_unchanged_snapshot_preserves_the_pending_mute_guard(device, hass):
    bind_shared(device)
    hass.states.async_set(
        device.entity_id,
        "on",
        {"volume_level": 0.42, "is_volume_muted": False},
    )
    device.async_send_command = AsyncMock(return_value=True)
    await device.async_mute_volume(True)
    device._external_audio_ip_updated()
    await device.async_mute_volume(True)
    device.async_send_command.assert_awaited_once_with("KEY_MUTE")


@pytest.mark.parametrize(
    ("token", "enabled", "available"),
    [("synthetic", True, True), ("synthetic", False, False), (None, True, False)],
)
def test_shared_audio_requires_enabled_pairing(device, hass, token, enabled, available):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.samsungtv_smart.const import (
        CONF_ENABLE_IP_CONTROL,
        CONF_IP_CONTROL_TOKEN,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_IP_CONTROL_TOKEN: token},
        options={CONF_ENABLE_IP_CONTROL: enabled},
    )
    entry.add_to_hass(hass)
    device._entry_id = entry.entry_id
    assert (device._external_audio_ip_entry() is entry) is available


async def test_audio_properties_only_read_the_cached_feedback(device, hass):
    await hass.async_block_till_done()
    device._external_audio_feedback = MagicMock(
        side_effect=AssertionError("property recomputed feedback")
    )
    assert device.volume_level == 0.42
    assert device.is_volume_muted is False
    device._external_audio_feedback.assert_not_called()


async def test_receiver_unavailability_invalidates_the_cache_without_tv_reads(
    device, hass
):
    hass.states.async_set(device._external_audio_entity, "unavailable")
    await hass.async_block_till_done()
    assert device.volume_level is None
    assert device.is_volume_muted is None
    device._get_ip_control_client.assert_not_called()
    device._upnp.async_get_volume.assert_not_awaited()


async def test_speaker_output_events_refresh_cached_audio(device, hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.samsungtv_smart.const import DOMAIN
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    device._entry_id = entry.entry_id
    select = er.async_get(hass).async_get_or_create(
        "select", DOMAIN, "synthetic_ip_control_speaker_select", config_entry=entry
    )
    del device._speaker_output_is_internal
    hass.states.async_set(select.entity_id, "Internal")
    device._attach_external_audio_listener()
    assert device.volume_level is None
    hass.states.async_set(select.entity_id, "External")
    await hass.async_block_till_done()
    assert device.volume_level == 0.42
    device._get_ip_control_client.assert_not_called()
