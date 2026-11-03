import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from aiohttp import ClientConnectionError
import pytest

from custom_components.samsungtv_smart import media_player
from custom_components.samsungtv_smart.api.samsungws import HttpApiError, ResponseError
from custom_components.samsungtv_smart.const import DEFAULT_APP
from custom_components.samsungtv_smart.media_player import SamsungTVDevice
from homeassistant.components.media_player import MediaPlayerState, MediaType

APP_ID = "com.example.player"


@pytest.fixture
def clock(monkeypatch):
    current = [100.0]
    monkeypatch.setattr(
        media_player, "time", SimpleNamespace(monotonic=lambda: current[0])
    )
    return current


@pytest.fixture
def device(clock):
    entity = object.__new__(SamsungTVDevice)
    entity._host = "192.0.2.10"
    entity._entry_id = "synthetic-entry"
    entity._device_info = None
    entity._get_ip_control_picture_mode = Mock(return_value=None)
    entity._log = Mock()
    entity._state = MediaPlayerState.ON
    entity._rest_app_list = {"Video player": APP_ID}
    entity._running_app = DEFAULT_APP
    entity._app_list = {}
    entity._app_list_st = {}
    entity._ws = SimpleNamespace(is_app_running=Mock(return_value=None))
    entity._st = None
    entity._source_list = {"HDMI input": "IP_HDMI1"}
    entity._get_ip_control_input_source = Mock(return_value="HDMI1")
    entity._ip_art_mode = False
    entity._ip_art_mode_at = None
    entity._power_off_in_progress = Mock(return_value=False)
    entity._rest_api = SimpleNamespace(
        async_rest_app_status=AsyncMock(
            return_value={
                "id": APP_ID,
                "name": "Video player",
                "running": True,
                "visible": True,
            }
        )
    )
    return entity


async def test_foreground_app_has_app_metadata_and_a_separate_hdmi_source(device):
    await device._async_update_rest_app_status()

    device._rest_api.async_rest_app_status.assert_awaited_once_with(
        "com.example.player"
    )
    assert device.app_name == "Video player"
    assert device.app_id == "com.example.player"
    assert device.media_content_type == MediaType.APP
    assert device._get_new_media_title() == "Video player"
    assert device.source == "HDMI input"
    assert device.state == MediaPlayerState.ON


async def test_running_but_hidden_app_is_not_the_current_app(device, clock):
    await device._async_update_rest_app_status()
    device._rest_api.async_rest_app_status.return_value["visible"] = False
    clock[0] += 30

    await device._async_update_rest_app_status()

    assert device.app_name is None
    assert device.app_id == DEFAULT_APP
    assert device._get_new_media_title() == "HDMI input"
    assert device.source == "HDMI input"


@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        "not supported",
        {},
        {"error": "not found"},
        {"id": "another.app", "visible": True},
        {"id": APP_ID, "running": True},
        {"id": APP_ID, "visible": "true"},
        {"id": APP_ID, "visible": 1},
    ],
)
async def test_missing_or_invalid_status_clears_old_app_metadata(device, reply):
    device._rest_app_id = APP_ID
    device._rest_app_name = "Video player"
    device._rest_api.async_rest_app_status.return_value = reply

    await device._async_update_rest_app_status()

    assert device.app_name is None
    assert device.app_id == DEFAULT_APP
    assert device.source == "HDMI input"


@pytest.mark.parametrize(
    "error",
    [
        asyncio.TimeoutError(),
        ClientConnectionError("unreachable"),
        HttpApiError("not supported"),
        ResponseError("invalid JSON"),
        ValueError("invalid reply"),
    ],
)
async def test_failed_poll_does_not_latch_app_or_fail_the_entity(device, error):
    device._rest_app_id = APP_ID
    device._rest_app_name = "Video player"
    device._rest_api.async_rest_app_status.side_effect = error

    await device._async_update_rest_app_status()

    assert device.app_name is None
    assert device.app_id == DEFAULT_APP
    assert device.state == MediaPlayerState.ON
    assert device.source == "HDMI input"


async def test_slow_app_endpoint_has_a_bounded_timeout(device, monkeypatch):
    monkeypatch.setattr(media_player, "REST_APP_STATUS_TIMEOUT", 0.01)

    async def silent_endpoint(_app_id):
        await asyncio.Event().wait()

    device._rest_api.async_rest_app_status.side_effect = silent_endpoint
    device._rest_app_id = APP_ID
    device._rest_app_name = "Video player"

    await asyncio.wait_for(device._async_update_rest_app_status(), timeout=0.5)

    assert device.app_name is None
    assert device.source == "HDMI input"


@pytest.mark.parametrize("power_off_pending", [False, True])
async def test_off_or_powering_off_tv_is_not_probed(device, power_off_pending):
    device._rest_app_id = APP_ID
    device._rest_app_name = "Video player"
    if power_off_pending:
        device._power_off_in_progress.return_value = True
    else:
        device._state = MediaPlayerState.OFF

    await device._async_update_rest_app_status()

    device._rest_api.async_rest_app_status.assert_not_awaited()
    assert device.app_name is None
    assert device.app_id == (DEFAULT_APP if power_off_pending else None)


async def test_visible_app_overrules_stale_art_feedback(device, clock):
    device._ip_art_mode = True

    await device._async_update_rest_app_status()

    assert device._art_mode_is_on() is False
    assert device.state == MediaPlayerState.ON

    device._rest_api.async_rest_app_status.return_value["visible"] = False
    clock[0] += 30
    await device._async_update_rest_app_status()

    assert device._art_mode_is_on() is True
    assert device.app_name is None


def test_existing_cloud_app_id_fallback_is_unchanged(device):
    device._st = SimpleNamespace(channel="", channel_name="art")

    assert device.app_name is None
    assert device.app_id == "art"


def test_discovered_apps_keep_the_existing_source_semantics(device):
    device._running_app = "Discovered app"
    device._app_list = {"Discovered app": "com.example.discovered"}
    device._app_list_st = dict(device._app_list)

    assert device.app_name == "Discovered app"
    assert device.app_id == "com.example.discovered"
    assert device.source == "Discovered app"
    assert device.extra_state_attributes["input_source"] == "HDMI1"
    assert device._get_new_media_title() == "Discovered app"


def test_unmapped_physical_input_is_reported_without_becoming_the_app(device):
    device._rest_app_id = APP_ID
    device._rest_app_name = "Video player"
    device._get_ip_control_input_source.return_value = "HDMI3"

    assert device.extra_state_attributes["input_source"] == "HDMI3"
    assert device.app_name == "Video player"


def test_app_source_fallback_remains_available_without_a_local_input(device):
    device._get_ip_control_input_source.return_value = None
    device._running_app = "Discovered app"

    assert device.source == "Discovered app"


async def test_empty_mapping_disables_all_additional_requests(device):
    device._rest_app_list = {}
    device._rest_app_id = APP_ID
    device._rest_app_name = "Video player"

    await device._async_update_rest_app_status()

    device._rest_api.async_rest_app_status.assert_not_awaited()
    assert device.app_name is None


def test_existing_app_as_source_behavior_can_be_retained(device):
    device._running_app = "Discovered app"

    assert device.source == "Discovered app"


async def test_one_silent_app_does_not_starve_other_configured_apps(
    device, monkeypatch, clock
):
    monkeypatch.setattr(media_player, "REST_APP_STATUS_TIMEOUT", 0.01)
    device._rest_app_list = {"Slow": "com.example.slow", "Ready": "com.example.ready"}

    async def fetch(app_id):
        if app_id == "com.example.slow":
            await asyncio.Event().wait()
        return {"id": app_id, "visible": True}

    device._rest_api.async_rest_app_status.side_effect = fetch
    await asyncio.wait_for(device._async_update_rest_app_status(), timeout=0.5)
    assert device.app_name is None
    clock[0] += 30
    await asyncio.wait_for(device._async_update_rest_app_status(), timeout=0.5)

    assert device.app_name == "Ready"
    assert device.app_id == "com.example.ready"
    assert [
        call.args[0] for call in device._rest_api.async_rest_app_status.await_args_list
    ] == ["com.example.slow", "com.example.ready"]


def test_disabled_source_separation_keeps_the_existing_fallback_app_id(device):
    device._rest_app_list = {}
    assert device.app_id == DEFAULT_APP


async def test_poll_results_are_cached_until_the_app_interval_expires(device, clock):
    await device._async_update_rest_app_status()
    clock[0] += 29
    await device._async_update_rest_app_status()
    assert device.app_name == "Video player"
    assert device._rest_api.async_rest_app_status.await_count == 1
    clock[0] += 1
    await device._async_update_rest_app_status()
    assert device._rest_api.async_rest_app_status.await_count == 2


async def test_art_transition_allows_a_prompt_app_status_refresh(device, clock):
    await device._async_update_rest_app_status()
    clock[0] += 1
    device.async_write_ha_state = Mock()
    device._refresh_ip_art_mode = Mock(return_value=object())
    device.hass = SimpleNamespace(async_create_task=Mock())
    device._on_art_transition()
    await device._async_update_rest_app_status()
    assert device._rest_api.async_rest_app_status.await_count == 2


async def test_power_off_clears_the_cached_app_and_refreshes_on_return(device, clock):
    await device._async_update_rest_app_status()
    device._state = MediaPlayerState.OFF
    await device._async_update_rest_app_status()
    assert device.app_name is None
    device._state = MediaPlayerState.ON
    clock[0] += 1
    await device._async_update_rest_app_status()
    assert device._rest_api.async_rest_app_status.await_count == 2


async def test_local_app_selection_does_not_publish_the_previous_extra_app(device):
    await device._async_update_rest_app_status()
    device._source_list = {}
    device._app_list = {"New app": "com.example.new"}
    device._app_list_st = dict(device._app_list)
    device._async_launch_app = AsyncMock()
    device.async_write_ha_state = Mock()
    titles = []

    async def update_media():
        titles.append(device._get_new_media_title())

    device._update_media = update_media
    await device._async_select_source("New app", assume_on=True)

    device._async_launch_app.assert_awaited_once_with("com.example.new")
    assert titles == ["New app"]
    assert device.app_name == "New app"
    assert device.app_id == "com.example.new"
    assert device._rest_app_last_poll is None


@pytest.mark.parametrize("visible", [True, False])
async def test_native_app_status_does_not_need_a_second_rest_read(device, visible):
    device._app_list = {"Discovered app": APP_ID}
    device._app_list_st = dict(device._app_list)
    device._ws.is_app_running.return_value = visible

    await device._async_update_rest_app_status()
    device._get_running_app()

    device._rest_api.async_rest_app_status.assert_not_awaited()
    assert device._rest_app_id is None
    assert device.app_name == ("Discovered app" if visible else None)


async def test_native_app_without_status_can_still_use_the_rest_fallback(device):
    device._app_list = {"Discovered app": APP_ID}
    device._app_list_st = dict(device._app_list)

    await device._async_update_rest_app_status()
    device._get_running_app()

    device._rest_api.async_rest_app_status.assert_awaited_once_with(APP_ID)
    assert device.app_name == "Video player"


@pytest.mark.parametrize("running", [True, False])
async def test_stale_native_status_keeps_the_rest_fallback(device, running):
    now = datetime.now(timezone.utc)
    native = object.__new__(media_player.SamsungTVWS)
    native._running_app = APP_ID if running else None
    native._running_apps = {APP_ID: now - timedelta(seconds=31)}
    native._last_running_scan = now
    device._ws = native
    device._app_list = {"Discovered app": APP_ID}

    await device._async_update_rest_app_status()

    device._rest_api.async_rest_app_status.assert_awaited_once_with(APP_ID)
    assert device.app_name == "Video player"


async def test_only_untracked_apps_get_additional_requests(device):
    device._rest_app_list = {"Known app": "com.example.known", "Video player": APP_ID}
    device._app_list = {"Known app": "com.example.known"}
    device._ws.is_app_running.return_value = False

    await device._async_update_rest_app_status()

    device._rest_api.async_rest_app_status.assert_awaited_once_with(APP_ID)
    assert device.app_name == "Video player"


async def test_native_tracker_takes_over_before_the_rest_cache_expires(device, clock):
    await device._async_update_rest_app_status()
    clock[0] += 1
    device._app_list = {"Discovered app": APP_ID}
    device._app_list_st = dict(device._app_list)
    device._ws.is_app_running.return_value = False

    await device._async_update_rest_app_status()
    device._get_running_app()

    assert device._rest_api.async_rest_app_status.await_count == 1
    assert device._rest_app_id is None
    assert device.app_name is None


@pytest.mark.parametrize("old_status", [True, None])
async def test_extra_app_checks_preserve_the_upstream_launch_hold(
    device, clock, old_status
):
    device._app_list = {"Old app": APP_ID, "New app": "com.example.new"}
    device._app_list_st = dict(device._app_list)
    device._running_app = "New app"
    device._local_app_replaced = "Old app"
    device._local_app_hold_until = clock[0] + 20
    device._rest_app_id = APP_ID
    device._rest_app_name = "Old app"
    device._ws.is_app_running.side_effect = lambda app_id, **_kwargs: (
        old_status if app_id == APP_ID else None
    )

    await device._async_update_rest_app_status()
    device._get_running_app()

    device._rest_api.async_rest_app_status.assert_not_awaited()
    assert device.app_name == "New app"
    assert device._get_new_media_title() == "New app"


def native_tracker(app_id, *, age):
    now = datetime.now(timezone.utc)
    tracker = object.__new__(media_player.SamsungTVWS)
    tracker._running_app = app_id
    tracker._running_apps = {app_id: now - timedelta(seconds=age)}
    tracker._last_running_scan = now
    return tracker


async def test_a_new_native_app_replaces_cached_rest_metadata(device, clock):
    await device._async_update_rest_app_status()
    clock[0] += 1
    device._app_list = {"Native player": "com.example.native"}
    device._app_list_st = dict(device._app_list)
    device._ws = native_tracker("com.example.native", age=0.1)

    device._get_running_app()
    await device._async_update_rest_app_status()

    assert device.app_name == "Native player"
    assert device.app_id == "com.example.native"
    assert device._get_new_media_title() == "Native player"
    assert device.source == "Native player"
    assert device.extra_state_attributes["input_source"] == "HDMI1"
    assert device._rest_api.async_rest_app_status.await_count == 1


async def test_an_older_native_scan_does_not_replace_a_fresh_rest_reading(
    device, clock
):
    await device._async_update_rest_app_status()
    clock[0] += 1
    device._app_list = {"Native player": "com.example.native"}
    device._app_list_st = dict(device._app_list)
    device._ws = native_tracker("com.example.native", age=10)

    device._get_running_app()

    assert device.app_name == "Video player"
    assert device.app_id == APP_ID
    assert device._get_new_media_title() == "Video player"
