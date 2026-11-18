"""The IP Control state poll corrects a stale Gen_<n> pictureMode (#447).

The media player's cached art state, the picture mode entities and the tuner
check all read the getTVStates snapshot this coordinator holds, and none of
them reads the artModeControl flag. With art on the panel and the snapshot
stuck on ``Gen_327705`` they all said "not art". The poll asks
``SamsungIPControl.async_generated_mode_is_art`` once and, when that says art,
stores ``Ambient``, so every reader gets the same answer without a change of
its own. The client-side cases are in
test_art_mode_stale_generated_picture_mode.py.
"""

from unittest.mock import AsyncMock

import pytest

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
