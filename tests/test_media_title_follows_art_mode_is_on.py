"""The media title must not say "Art Mode" once the local signal says art is off.

Measured (192.168.1.161, 2026-10-05): art switched off at 22:13:48 (confirmed
by the TV's own log: ambient mode type FRAME_TV -> Undefined), art_mode_status
read off from then on, yet the media title stayed "Art Mode" until 22:14:20.
_get_new_media_title() mapped SmartThings' running app "art" to the Art Mode
title regardless of the local verdict, and the cloud lags 30-45 s.

The extra_state_attributes comment states the title MUST follow the same logic
as art_mode_status; the SmartThings "art" mapping now applies only when the
local signal is unknown.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

import pytest  # noqa: E402

from custom_components.samsungtv_smart.api.smartthings import STStatus  # noqa: E402
from custom_components.samsungtv_smart.const import DEFAULT_APP  # noqa: E402
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    ART_MODE_MEDIA_TITLE,
    SamsungTVDevice,
)
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402


def _device(channel_name="art"):
    device = object.__new__(SamsungTVDevice)
    device._state = MediaPlayerState.ON
    device._running_app = DEFAULT_APP
    device._app_list_st = {}
    # Still reporting the art app 30-45 s after the panel left it.
    device._st = SimpleNamespace(
        state=STStatus.STATE_ON, source="HDMI3", channel_name=channel_name
    )
    return device


def _title(device, art_on):
    with (
        patch.object(SamsungTVDevice, "_art_mode_is_on", return_value=art_on),
        patch.object(SamsungTVDevice, "_get_source", return_value="CINEMA 60"),
        patch.object(SamsungTVDevice, "_resolve_app_name", return_value=None),
    ):
        return device._get_new_media_title()


def test_replay_of_22_14_00_a_lagging_cloud_art_does_not_override_local_off():
    assert _title(_device(), art_on=False) == "CINEMA 60"


def test_cloud_art_still_names_art_mode_when_nothing_local_is_known():
    assert _title(_device(), art_on=None) == ART_MODE_MEDIA_TITLE


def test_local_art_on_wins_whatever_the_cloud_says():
    assert _title(_device(channel_name="netflix"), art_on=True) == (
        ART_MODE_MEDIA_TITLE
    )


@pytest.mark.parametrize("art_on", [False, None])
def test_a_real_cloud_app_is_still_named(art_on):
    with (
        patch.object(SamsungTVDevice, "_art_mode_is_on", return_value=art_on),
        patch.object(SamsungTVDevice, "_resolve_app_name", return_value="Netflix"),
    ):
        assert _device(channel_name="org.netflix")._get_new_media_title() == (
            "Netflix"
        )
