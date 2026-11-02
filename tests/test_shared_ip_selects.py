import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.samsungtv_smart import select as select_module
from custom_components.samsungtv_smart import shared_ip
from custom_components.samsungtv_smart.api.ipcontrol import (
    SamsungIPControlError,
    SamsungIPControlUnsupportedError,
)
from custom_components.samsungtv_smart.const import (
    CONF_IP_CONTROL_TOKEN,
    DATA_IP_CONTROL_STATE_COORDINATOR,
    DOMAIN,
)


class Coordinator:
    def __init__(self, clock):
        self.clock = clock
        self.listeners = []
        self.last_update_success = True
        self.data = {
            "tv": {"pictureMode": "Movie", "speakerSelect": "internal"},
            "powered_off": False,
            "polled_at": clock[0],
        }

    def async_add_listener(self, callback):
        self.listeners.append(callback)
        return lambda: self.listeners.remove(callback)

    def emit(self, **fields):
        self.data["tv"].update(fields)
        self.data["polled_at"] = self.clock[0]
        for callback in tuple(self.listeners):
            callback()


@pytest.fixture
async def setup_select(monkeypatch):
    clock = [1000.0]
    normal = [True]
    fake_time = SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0])
    monkeypatch.setattr(shared_ip, "time", fake_time)
    monkeypatch.setattr(select_module, "time", fake_time)
    monkeypatch.setattr(select_module, "_tv_normal_viewing", lambda *_: normal[0])
    monkeypatch.setattr(select_module, "clear_token_problem", Mock())
    monkeypatch.setattr(select_module, "notify_token_problem", Mock())
    discovery_unsub = Mock()
    tracker = Mock(return_value=discovery_unsub)
    monkeypatch.setattr(select_module, "async_track_time_interval", tracker)
    entry = SimpleNamespace(
        entry_id="example",
        title="Example TV",
        data={CONF_IP_CONTROL_TOKEN: "synthetic"},
        options={},
    )
    coordinator = Coordinator(clock)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda _: entry),
        data={
            DOMAIN: {entry.entry_id: {DATA_IP_CONTROL_STATE_COORDINATOR: coordinator}}
        },
        async_create_task=asyncio.create_task,
    )
    created = []

    async def create(kind="picture", error=None, force=False):
        cls = (
            select_module.SamsungTVIPControlPictureModeSelect
            if kind == "picture"
            else select_module.SamsungTVIPControlSpeakerSelect
        )
        entity = cls(hass, entry, "192.0.2.10", "Example TV", "example-tv")
        entity._attr_force_update = force
        client = AsyncMock()
        client.async_get_picture_mode.return_value = "Movie"
        client.async_set_picture_mode.return_value = "Movie"
        client.async_get_speaker_select.return_value = "Internal"
        client.async_get_external_speakers.return_value = [
            {"deviceName": "Receiver", "deviceId": "receiver-1"}
        ]
        if error:
            client.async_get_picture_mode.side_effect = error
        entity._get_ip_control = Mock(return_value=client)
        entity.async_write_ha_state = Mock()
        await entity.async_update()
        entity.entity_id = "select.example_" + kind
        await entity.async_added_to_hass()
        created.append(entity)
        return entity, client

    yield SimpleNamespace(
        create=create,
        clock=clock,
        normal=normal,
        entry=entry,
        coordinator=coordinator,
        hass=hass,
        tracker=tracker,
        discovery_unsub=discovery_unsub,
    )
    for entity in created:
        await entity.async_will_remove_from_hass()


async def test_picture_mode_uses_shared_updates_without_getter_requests(setup_select):
    env = setup_select
    entity, client = await env.create()
    env.clock[0] += 10
    env.coordinator.emit(pictureMode="StandardHDR")
    assert entity.current_option == "standard"
    assert entity.extra_state_attributes["tv_picture_mode"] == "StandardHDR"
    assert client.async_get_picture_mode.await_count == 1
    assert entity.should_poll is False


async def test_shared_updates_keep_unknown_but_reported_picture_modes(setup_select):
    env = setup_select
    entity, _ = await env.create()
    env.clock[0] += 10
    env.coordinator.emit(pictureMode="Natural")
    assert entity.current_option == "natural"
    assert "natural" in entity.options


async def test_fallback_deadline_does_not_follow_a_slower_shared_interval(setup_select):
    env = setup_select
    env.entry.options["ip_control_poll_interval"] = 600
    entity, client = await env.create()
    env.clock[0] = 1005
    env.coordinator.emit(pictureMode="Standard")
    env.clock[0] = 1034.9
    await entity._async_shared_fallback()
    assert client.async_get_picture_mode.await_count == 1
    env.clock[0] = 1035
    await entity._async_shared_fallback()
    assert client.async_get_picture_mode.await_count == 2
    assert entity._shared_read_window.interval == 30


async def test_a_late_shared_reply_does_not_extend_the_original_deadline(setup_select):
    env = setup_select
    entity, client = await env.create()
    env.clock[0] = 1025
    env.coordinator.data["tv"]["pictureMode"] = "Standard"
    env.coordinator.data["polled_at"] = 1005
    entity._shared_snapshot_changed()
    env.clock[0] = 1035
    await entity._async_shared_fallback()
    assert client.async_get_picture_mode.await_count == 2


@pytest.mark.parametrize("value", [None, "", "unknown", "unavailable", True, "Ambient"])
async def test_unusable_shared_values_keep_the_existing_getter(setup_select, value):
    env = setup_select
    entity, client = await env.create()
    env.clock[0] = 1010
    env.coordinator.emit(pictureMode=value)
    assert entity.current_option == "movie"
    env.clock[0] = 1030
    await entity._async_shared_fallback()
    assert client.async_get_picture_mode.await_count == 2


@pytest.mark.parametrize("observed", [None, "1000", True, float("nan"), 1050])
async def test_unusable_timestamps_do_not_suppress_the_fallback(setup_select, observed):
    env = setup_select
    entity, client = await env.create()
    env.clock[0] = 1010
    env.coordinator.data["tv"]["pictureMode"] = "Standard"
    env.coordinator.data["polled_at"] = observed
    entity._shared_snapshot_changed()
    assert entity.current_option == "movie"
    env.clock[0] = 1030
    await entity._async_shared_fallback()
    assert client.async_get_picture_mode.await_count == 2


async def test_failed_shared_poll_cannot_extend_the_fallback_deadline(setup_select):
    env = setup_select
    entity, client = await env.create()
    env.clock[0] = 1029
    env.coordinator.last_update_success = False
    env.coordinator.emit(pictureMode="Standard")
    env.clock[0] = 1030
    await entity._async_shared_fallback()
    assert client.async_get_picture_mode.await_count == 2


async def test_missing_or_late_sensor_platform_keeps_the_original_fallback(
    setup_select,
):
    env = setup_select
    data = env.hass.data[DOMAIN][env.entry.entry_id]
    data.pop(DATA_IP_CONTROL_STATE_COORDINATOR)
    entity, client = await env.create()
    assert not env.coordinator.listeners
    env.clock[0] += 30
    await entity._async_shared_fallback()
    assert client.async_get_picture_mode.await_count == 2
    data[DATA_IP_CONTROL_STATE_COORDINATOR] = env.coordinator
    entity._bind_shared_reads()
    env.clock[0] += 10
    env.coordinator.emit(pictureMode="Standard")
    assert entity.current_option == "standard"


async def test_replacing_the_coordinator_removes_the_old_listener(setup_select):
    env = setup_select
    entity, _ = await env.create()
    old = env.coordinator
    newer = Coordinator(env.clock)
    env.hass.data[DOMAIN][env.entry.entry_id][DATA_IP_CONTROL_STATE_COORDINATOR] = newer
    env.clock[0] += 10
    old.emit(pictureMode="Standard")
    assert old.listeners == []
    assert len(newer.listeners) == 1
    newer.emit(pictureMode="Natural")
    assert entity.current_option == "natural"


async def test_explicit_entity_updates_keep_fresh_getter_behavior(setup_select):
    env = setup_select
    entity, client = await env.create()
    env.clock[0] += 10
    env.coordinator.emit(pictureMode="Standard")
    client.async_get_picture_mode.return_value = "Movie"
    await entity.async_update()
    assert client.async_get_picture_mode.await_count == 2
    assert entity.current_option == "movie"


async def test_old_snapshot_does_not_undo_a_picture_mode_command(setup_select):
    env = setup_select
    entity, client = await env.create()
    env.clock[0] = 1010
    env.coordinator.emit(pictureMode="Standard")
    env.clock[0] = 1020
    await entity.async_select_option("movie")
    client.async_set_picture_mode.assert_awaited_once()
    env.coordinator.data["tv"]["pictureMode"] = "Standard"
    env.coordinator.data["polled_at"] = 1019
    entity._shared_snapshot_changed()
    assert entity.current_option == "movie"


async def test_an_explicit_refresh_is_not_discarded_after_a_shared_update(setup_select):
    env = setup_select
    entity, client = await env.create()
    started, release = asyncio.Event(), asyncio.Event()

    async def read():
        started.set()
        await release.wait()
        return "Movie"

    client.async_get_picture_mode.side_effect = read
    env.clock[0] = 1010
    update = asyncio.create_task(entity.async_update())
    await started.wait()
    env.clock[0] = 1011
    env.coordinator.emit(pictureMode="Standard")
    env.clock[0] = 1012
    release.set()
    await update
    assert entity.current_option == "movie"


async def test_a_command_reply_is_not_undone_by_an_older_inflight_read(setup_select):
    env = setup_select
    entity, client = await env.create()
    started, release = asyncio.Event(), asyncio.Event()

    async def read():
        started.set()
        await release.wait()
        return "Standard"

    client.async_get_picture_mode.side_effect = read
    env.clock[0] = 1010
    update = asyncio.create_task(entity.async_update())
    await started.wait()
    env.clock[0] = 1011
    await entity.async_select_option("movie")
    env.clock[0] = 1012
    release.set()
    await update
    assert entity.current_option == "movie"


async def test_art_mode_skips_control_polling_and_recovers_from_shared_data(
    setup_select,
):
    env = setup_select
    entity, client = await env.create()
    env.normal[0] = False
    env.clock[0] += 10
    env.coordinator.emit(pictureMode="Ambient")
    assert not entity.available
    assert client.async_get_picture_mode.await_count == 1
    env.normal[0] = True
    env.clock[0] += 10
    env.coordinator.emit(pictureMode="Movie")
    assert entity.available
    assert entity.current_option == "movie"
    assert client.async_get_picture_mode.await_count == 1


async def test_shared_reading_does_not_enable_a_previously_unsupported_control(
    setup_select,
):
    env = setup_select
    entity, client = await env.create(
        error=SamsungIPControlUnsupportedError("not supported")
    )
    env.clock[0] += 10
    env.coordinator.emit(pictureMode="Movie")
    assert not entity.available
    assert not entity._shared_read_ready
    assert client.async_get_picture_mode.await_count == 1


async def test_failed_fallback_keeps_retrying_and_can_recover(setup_select):
    env = setup_select
    entity, client = await env.create()
    env.coordinator.last_update_success = False
    client.async_get_picture_mode.side_effect = SamsungIPControlError(
        "temporary failure"
    )
    env.clock[0] += 30
    await entity._async_shared_fallback()
    assert not entity.available
    client.async_get_picture_mode.side_effect = None
    env.clock[0] += 30
    await entity._async_shared_fallback()
    assert entity.available
    assert client.async_get_picture_mode.await_count == 3


async def test_speaker_discovery_remains_on_its_original_cadence(setup_select):
    env = setup_select
    entity, client = await env.create("speaker")
    for i in range(1, 61):
        env.clock[0] = 1000 + i * 10
        env.coordinator.emit(speakerSelect="external")
        if i % 3 == 0:
            await entity._async_discover()
    assert entity.current_option == "Receiver"
    assert "Receiver" in entity.options
    assert client.async_get_speaker_select.await_count == 1
    assert client.async_get_external_speakers.await_count == 21
    assert env.tracker.call_args.args[2].total_seconds() == 30


async def test_startup_status_does_not_expose_external_before_discovery(setup_select):
    env = setup_select
    env.normal[0] = False
    entity, client = await env.create("speaker")
    env.normal[0] = True
    client.async_get_speaker_select.return_value = "External"
    env.clock[0] += 30
    env.coordinator.emit(speakerSelect="external")
    await entity._async_shared_fallback()
    assert not entity.available
    assert entity.current_option is None
    client.async_get_external_speakers.assert_not_awaited()
    await entity._async_discover()
    assert entity.available
    assert entity.current_option == "Receiver"
    assert "Receiver" in entity.options


async def test_speaker_choices_still_refresh_when_new_devices_appear(setup_select):
    env = setup_select
    entity, client = await env.create("speaker")
    client.async_get_external_speakers.return_value.append(
        {"deviceName": "Second receiver", "deviceId": "receiver-2"}
    )
    env.clock[0] += 10
    env.coordinator.emit(speakerSelect="receiver-2")
    await entity._async_discover()
    assert "Second receiver" in entity.options
    assert entity.current_option == "Second receiver"


async def test_unknown_external_output_does_not_replace_a_specific_receiver(
    setup_select,
):
    env = setup_select
    entity, _ = await env.create("speaker")
    entity._external_devices = {"Receiver": "receiver-1", "Second": "receiver-2"}
    entity._attr_current_option = "Receiver"
    env.clock[0] += 10
    env.coordinator.emit(speakerSelect="external")
    assert entity.current_option == "Receiver"


async def test_reload_removes_listeners_and_timers(setup_select):
    env = setup_select
    entity, _ = await env.create("speaker")
    assert len(env.coordinator.listeners) == 1
    await entity.async_will_remove_from_hass()
    assert not env.coordinator.listeners
    assert entity._shared_read_window is None
    env.discovery_unsub.assert_called_once()
    env.clock[0] += 10
    entity.async_write_ha_state.reset_mock()
    env.coordinator.emit(speakerSelect="internal")
    entity.async_write_ha_state.assert_not_called()


async def test_force_update_keeps_the_original_polling_and_requests(setup_select):
    env = setup_select
    entity, client = await env.create("speaker", force=True)
    assert entity.should_poll is True
    assert entity._shared_read_window is None
    assert not env.coordinator.listeners
    env.tracker.assert_not_called()
    await entity.async_update()
    assert client.async_get_speaker_select.await_count == 2
    assert client.async_get_external_speakers.await_count == 2
