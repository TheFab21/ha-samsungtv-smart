"""The IP Control state poll corrects a stale Gen_<n> pictureMode (#447).

The media player's cached art state, the picture mode entities and the tuner
check all read the getTVStates snapshot this coordinator holds, and none of
them reads the artModeControl flag. With art on the panel and the snapshot
stuck on ``Gen_327705`` they all said "not art". The poll asks
``SamsungIPControl.async_generated_mode_is_art`` once and, when that says art,
stores ``Ambient``, so every reader gets the same answer without a change of
its own. The client-side cases are in
test_art_mode_stale_generated_picture_mode.py.

A TV on its home screen or in an app reports a Gen_ id on every poll, so the
poll does not ask while art is known to be off: the previous snapshot showed a
mode other than Ambient and no art_mode_changed ON broadcast came after it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.samsungtv_smart.const import DATA_ART_API, DOMAIN

from .test_ipcontrol_channel_coordinator import (
    HOST,
    IPControlStateCoordinator,
    _entry,
    _update,
)


def _client(snapshot, settled, *, source="HDMI1"):
    client = AsyncMock()
    client.async_get_power_state.return_value = "powerOn"
    client.async_get_tv_states.return_value = {
        "inputSource": source,
        "pictureMode": snapshot,
    }
    client.async_generated_mode_is_art.return_value = settled
    return client


async def test_a_stale_generated_mode_is_stored_as_ambient(hass):
    coordinator = IPControlStateCoordinator(hass, _entry(hass, is_frame=True), HOST)
    client = _client("Gen_327705", True)

    data = await _update(coordinator, client)

    assert data["tv"] == {"inputSource": "HDMI1", "pictureMode": "Ambient"}
    client.async_generated_mode_is_art.assert_awaited_once_with("Gen_327705")


async def test_the_answer_from_the_tv_is_not_modified_in_place(hass):
    coordinator = IPControlStateCoordinator(hass, _entry(hass, is_frame=True), HOST)
    client = _client("Gen_327705", True)

    await _update(coordinator, client)

    # getTVStates is a shared read: other callers hold the same dict.
    assert client.async_get_tv_states.return_value["pictureMode"] == "Gen_327705"


@pytest.mark.parametrize("settled", [False, None])
@pytest.mark.parametrize("snapshot", ["Gen_327705", "Gen_589826", "Movie"])
async def test_a_mode_that_is_not_settled_as_art_is_stored_as_read(
    hass, snapshot, settled
):
    coordinator = IPControlStateCoordinator(hass, _entry(hass, is_frame=True), HOST)
    client = _client(snapshot, settled)

    data = await _update(coordinator, client)

    assert data["tv"]["pictureMode"] == snapshot


async def test_the_tuner_is_not_polled_behind_a_stale_generated_mode(hass):
    """A Frame in art reports inputSource TV; the channel means nothing there."""
    coordinator = IPControlStateCoordinator(hass, _entry(hass, is_frame=True), HOST)
    client = _client("Gen_327705", True, source="TV")

    data = await _update(coordinator, client)

    assert data["channel"] == {}
    client.async_get_channel.assert_not_awaited()


# -- no artModeControl read while art is known to be off -------------------


def _coordinator(hass, *, previous, known_at=100.0, broadcast=None, art_api=True):
    """A coordinator holding a previous snapshot and a latest broadcast."""
    entry = _entry(hass, is_frame=True)
    coordinator = IPControlStateCoordinator(hass, entry, HOST)
    coordinator.data = previous
    if isinstance(previous, dict) and "polled_at" not in previous:
        previous["polled_at"] = known_at
    stored = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    if art_api:
        stored[DATA_ART_API] = SimpleNamespace(art_mode_last_broadcast=broadcast)
    return coordinator


def _snapshot(mode):
    return {"tv": {"pictureMode": mode}, "channel": {}, "powered_off": False}


@pytest.mark.parametrize("previous_mode", ["Gen_327705", "Gen_589826", "Movie"])
@pytest.mark.parametrize(
    "broadcast",
    [
        None,
        (False, 150.0),  # the newest broadcast says off
        (True, 99.0),  # ON, but older than the snapshot that showed art off
    ],
)
async def test_art_known_off_is_not_asked_about(hass, previous_mode, broadcast):
    coordinator = _coordinator(
        hass, previous=_snapshot(previous_mode), broadcast=broadcast
    )
    client = _client("Gen_327705", True)

    data = await _update(coordinator, client)

    client.async_generated_mode_is_art.assert_not_awaited()
    assert data["tv"]["pictureMode"] == "Gen_327705"


async def test_art_known_off_with_no_art_channel_is_not_asked_about(hass):
    coordinator = _coordinator(hass, previous=_snapshot("Gen_327705"), art_api=False)
    client = _client("Gen_327705", True)

    await _update(coordinator, client)

    client.async_generated_mode_is_art.assert_not_awaited()


@pytest.mark.parametrize("arrived", [100.0, 104.5])
async def test_an_on_broadcast_since_the_last_snapshot_is_asked_about(hass, arrived):
    """The #447 run: app closed, the TV announced art, the snapshot stayed."""
    coordinator = _coordinator(
        hass, previous=_snapshot("Gen_589826"), broadcast=(True, arrived)
    )
    client = _client("Gen_327705", True)

    data = await _update(coordinator, client)

    client.async_generated_mode_is_art.assert_awaited_once_with("Gen_327705")
    assert data["tv"]["pictureMode"] == "Ambient"


async def test_art_on_at_the_last_poll_is_asked_about(hass):
    """Ambient straight to Gen_327705, no broadcast (Nov 17 in #447)."""
    coordinator = _coordinator(hass, previous=_snapshot("Ambient"))
    client = _client("Gen_327705", True)

    data = await _update(coordinator, client)

    client.async_generated_mode_is_art.assert_awaited_once_with("Gen_327705")
    assert data["tv"]["pictureMode"] == "Ambient"


async def test_a_stuck_snapshot_is_asked_about_on_every_poll(hass):
    """The corrected snapshot is the "art was on" that keeps the check going."""
    coordinator = _coordinator(hass, previous=None)
    client = _client("Gen_327705", True)

    for _ in range(3):
        coordinator.data = await _update(coordinator, client)
        assert coordinator.data["tv"]["pictureMode"] == "Ambient"

    assert client.async_generated_mode_is_art.await_count == 3


@pytest.mark.parametrize(
    "previous",
    [
        None,
        {"tv": {}, "powered_off": True},
        {"tv": {}, "channel": {}, "powered_off": False},
        {"tv": {"pictureMode": ""}, "channel": {}, "powered_off": False},
        {"tv": {"pictureMode": "Movie"}, "powered_off": False, "polled_at": None},
    ],
)
async def test_an_unknown_art_state_is_asked_about(hass, previous):
    coordinator = _coordinator(hass, previous=previous)
    client = _client("Gen_327705", True)

    await _update(coordinator, client)

    client.async_generated_mode_is_art.assert_awaited_once_with("Gen_327705")
