"""The "Toggle Art Mode on Power" option makes media_player.toggle a TV <-> Art toggle.

The option used to act one way only: toggle while watching went to Art Mode,
but toggle while in Art Mode went to Art Mode again (a no-op on most Frames,
and a KEY_POWER that left Art Mode on others). With "Report media player off in
Art Mode" the state reads off while the art shows, so toggle tried to turn the
TV on instead. It now mirrors a short press of the Frame remote's power button:
Art Mode -> TV, TV -> Art Mode, standby -> on. Without the option, or on a
non-Frame TV, toggle is the plain power toggle.
"""

import asyncio
import sys
from types import ModuleType
from unittest.mock import MagicMock, PropertyMock, patch

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

from custom_components.samsungtv_smart.const import CONF_TOGGLE_ART_MODE  # noqa: E402
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    ArtModeSupport,
    SamsungTVDevice,
)
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402


def _device(*, option=True, art_on=False, art_error=None):
    device = object.__new__(SamsungTVDevice)
    device._log = MagicMock()
    device.calls = []
    device._get_option = lambda key, default=None: (
        option if key == CONF_TOGGLE_ART_MODE else default
    )
    device._art_mode_is_on = lambda: art_on

    async def record(name, *args):
        device.calls.append((name, *args))

    async def set_artmode(enabled):
        device.calls.append(("art_set_artmode", enabled))
        return {"error": art_error} if art_error else {"success": True}

    device.async_set_art_mode = lambda: record("to_art")
    device.async_art_set_artmode = set_artmode
    device.async_send_command = lambda key: record("key", key)
    device.async_turn_on = lambda: record("turn_on")
    device.async_turn_off = lambda: record("turn_off")
    return device


def _toggle(device, state, art=ArtModeSupport.FULL):
    with (
        patch.object(
            SamsungTVDevice, "state", new_callable=PropertyMock, return_value=state
        ),
        patch.object(
            SamsungTVDevice,
            "support_art_mode",
            new_callable=PropertyMock,
            return_value=art,
        ),
        patch.object(
            SamsungTVDevice,
            "supported_features",
            new_callable=PropertyMock,
            return_value=0xFFFFFFFF,
        ),
    ):
        asyncio.run(device.async_toggle())
    return device.calls


def test_watching_tv_goes_to_art_mode():
    assert _toggle(_device(), MediaPlayerState.ON) == [("to_art",)]


def test_art_mode_goes_back_to_the_tv():
    # The asymmetry: this used to ask for Art Mode again.
    calls = _toggle(_device(art_on=True), MediaPlayerState.ON)
    assert calls == [("art_set_artmode", False)]


def test_art_mode_reported_as_off_still_goes_back_to_the_tv():
    # "Report media player off in Art Mode": state is off while art shows, so
    # the plain toggle would have tried to turn the TV on.
    calls = _toggle(_device(art_on=True), MediaPlayerState.OFF)
    assert calls == [("art_set_artmode", False)]


def test_standby_still_turns_the_tv_on():
    assert _toggle(_device(), MediaPlayerState.OFF) == [("turn_on",)]


def test_a_partial_frame_leaves_art_mode_with_a_short_key_power():
    # No art channel to write to: the Frame's own short-press toggle.
    calls = _toggle(_device(art_on=True), MediaPlayerState.ON, ArtModeSupport.PARTIAL)
    assert calls == [("key", "KEY_POWER")]


def test_a_failed_art_exit_is_logged():
    device = _device(art_on=True, art_error="art channel down")
    _toggle(device, MediaPlayerState.ON)
    assert "art channel down" in str(device._log.warning.call_args)


def test_without_the_option_toggle_powers_off():
    calls = _toggle(_device(option=False, art_on=True), MediaPlayerState.ON)
    assert calls == [("turn_off",)]


def test_a_non_frame_tv_ignores_the_option():
    calls = _toggle(_device(), MediaPlayerState.ON, ArtModeSupport.UNSUPPORTED)
    assert calls == [("turn_off",)]
