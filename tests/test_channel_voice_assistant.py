"""Channel changes as voice assistants send them (#452).

Home Assistant's Alexa integration (Alexa.ChannelController) turns "switch to
channel 5" into media_player.play_media with media_content_type "channel" and a
bare "5", "put on Rai 1" into the same call with the channel's name, and "next
channel" into media_next_track. Three gaps dropped all of them silently:

- a bare number was refused while an app was running, and the TV usually
  powers on into the last app;
- a name was refused, even when the channel list maps it ("Rai 1": "1@TV");
- next/previous sent FF/REWIND on live TV whenever no channel number was
  reported.

Because the Alexa handler does not wait for the call, Alexa answered "OK" while
the TV did nothing. The failures that remain now raise, so they are visible.
"""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart import media_player as mp  # noqa: E402
from custom_components.samsungtv_smart.const import DEFAULT_APP  # noqa: E402
from custom_components.samsungtv_smart.media_player import SamsungTVDevice  # noqa: E402
from homeassistant.components.media_player import (  # noqa: E402
    MediaPlayerState,
    MediaType,
)
from homeassistant.exceptions import ServiceValidationError  # noqa: E402

NETFLIX = "Netflix"
CHANNELS = {"Rai 1": "1@TV", "Canale 5": "5@TV", "IPTV": "http://stream/live"}


def _device(*, running_app=DEFAULT_APP, source="TV", sources=None, st=None):
    device = object.__new__(SamsungTVDevice)
    device._log = MagicMock()
    device._state = MediaPlayerState.ON
    device._running_app = running_app
    device._rest_app_id = None
    device._source = running_app if running_app != DEFAULT_APP else source
    device._source_list = {"TV": "ST_TV"} if sources is None else sources
    device._channel_list = dict(CHANNELS)
    device._st = st
    device.keys = []
    device.selected = []

    def send_command(key, *args, **kwargs):
        device.keys.append(key)
        return True

    async def select_source(name):
        device.selected.append(name)
        device._source = name
        device._running_app = DEFAULT_APP

    async def executor(func, *args):
        return func(*args)

    device.send_command = send_command
    device.async_select_source = select_source
    device.async_play_media = AsyncMock()
    device._smartthings_keys = AsyncMock(return_value=True)
    device.hass = SimpleNamespace(async_add_executor_job=executor)
    return device


def _run(coro, media_channel=None):
    with (
        patch.object(
            SamsungTVDevice,
            "media_channel",
            new_callable=PropertyMock,
            return_value=media_channel,
        ),
        patch.object(mp, "KEYPRESS_DEFAULT_DELAY", 0),
        patch.object(mp.asyncio, "sleep", AsyncMock()),
    ):
        return asyncio.run(coro) if asyncio.iscoroutine(coro) else coro()


# --- 1. a bare number while an app runs --------------------------------------


def test_a_bare_number_from_an_app_switches_to_the_tuner_first():
    device = _device(running_app=NETFLIX)
    assert _run(device._async_set_channel("5")) is True
    assert device.selected == ["TV"]
    assert device.keys == ["KEY_5", "KEY_ENTER"]


def test_with_smartthings_the_channel_goes_out_as_an_st_key():
    device = _device(running_app=NETFLIX, st=object())
    _run(device._async_set_channel("5"))
    assert device.selected == ["TV"]
    device._smartthings_keys.assert_awaited_once_with("ST_CH5")


def test_on_the_tuner_a_bare_number_needs_no_source_switch():
    device = _device()
    _run(device._async_set_channel("12"))
    assert device.selected == []
    assert device.keys == ["KEY_1", "KEY_2", "KEY_ENTER"]


def test_without_a_tuner_source_it_raises_instead_of_failing_silently():
    device = _device(running_app=NETFLIX, sources={"HDMI1": "ST_HDMI1"})
    with pytest.raises(ServiceValidationError):
        _run(device._async_set_channel("5"))
    assert device.keys == []


def test_a_tuner_entry_is_found_by_its_key_whatever_its_name():
    device = _device(running_app=NETFLIX, sources={"Antenna": "KEY_TV"})
    _run(device._async_set_channel("5"))
    assert device.selected == ["Antenna"]


# --- 2. a channel by name -----------------------------------------------------


@pytest.mark.parametrize("name", ["Rai 1", "RAI1", "rai 1", "Rai-1"])
def test_a_channel_name_is_resolved_through_the_channel_list(name):
    device = _device()
    _run(device._async_set_channel(name))
    assert device.keys == ["KEY_1", "KEY_ENTER"]


def test_a_name_from_an_app_switches_to_the_tuner_then_tunes():
    device = _device(running_app=NETFLIX)
    _run(device._async_set_channel("Canale 5"))
    assert device.selected == ["TV"]
    assert device.keys == ["KEY_5", "KEY_ENTER"]


def test_a_name_that_maps_to_a_stream_url_plays_it():
    # Resolved before the URL test, or the URL would be rejected as a number.
    device = _device()
    _run(device._async_set_channel("IPTV"))
    device.async_play_media.assert_awaited_once_with(
        MediaType.URL, "http://stream/live"
    )


def test_an_unknown_name_raises():
    device = _device()
    with pytest.raises(ServiceValidationError):
        _run(device._async_set_channel("Nowhere TV"))
    assert device.keys == []


# --- 3. next / previous channel ----------------------------------------------


def test_next_and_previous_change_channel_on_the_tuner_without_a_number():
    device = _device()
    _run(device.media_next_track)
    _run(device.media_previous_track)
    assert device.keys == ["KEY_CHUP", "KEY_CHDOWN"]


def test_next_and_previous_still_seek_in_an_app():
    device = _device(running_app=NETFLIX)
    _run(device.media_next_track, media_channel="5")
    _run(device.media_previous_track, media_channel="5")
    assert device.keys == ["KEY_FF", "KEY_REWIND"]


def test_next_and_previous_seek_on_an_hdmi_input():
    device = _device(source="HDMI1", sources={"TV": "ST_TV", "HDMI1": "ST_HDMI1"})
    _run(device.media_next_track)
    assert device.keys == ["KEY_FF"]


# --- internal callers do not let the new exceptions escape ---------------------


def test_a_delayed_source_that_fails_is_logged_not_raised():
    # Runs from the update loop after a power-on: an exception would fail it.
    device = _device()
    device.async_select_source = AsyncMock(
        side_effect=ServiceValidationError("Unknown channel")
    )
    asyncio.run(device._async_select_source_delayed("Nowhere TV"))
    assert device._log.warning.called
