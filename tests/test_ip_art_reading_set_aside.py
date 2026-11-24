"""IP Control's Art Mode reading is set aside once it contradicts the TV (#466).

A 2024 Frame (QN55LS03D, firmware T-PTMDAKUC-0090-2130.0) with IP Control
paired: every read, every 5 s, artModeControl answered off and getTVStates
input HDMI1 / pictureMode Intelligent while the panel showed art, and
pictureModeControl / directVolumeControl answered -32601 as they do in art.
Switched with the remote, the TV broadcast art_mode_changed='on';
art_mode_status read on for the 20 s the broadcast outranks a reading, then
fell back to off for good. With IP Control disabled everything was right, but
the TV could no longer be powered off: KEY_POWER only toggles Art Mode there.
"""

import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

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

import pytest  # noqa: E402

from custom_components.samsungtv_smart import media_player as mp  # noqa: E402
from custom_components.samsungtv_smart.api.ipcontrol import (  # noqa: E402
    SamsungIPControlTransportError,
    SamsungIPControlUnsupportedError,
)
from custom_components.samsungtv_smart.api.samsungws import ArtModeStatus  # noqa: E402
from custom_components.samsungtv_smart.const import (  # noqa: E402
    DATA_ART_API,
    DATA_IP_ART_UNRELIABLE,
    DEFAULT_APP,
    DOMAIN,
)
from custom_components.samsungtv_smart.media_player import SamsungTVDevice  # noqa: E402
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402

ENTRY = "office"


def _refused():
    ex = SamsungIPControlUnsupportedError("Method not found")
    ex.code = -32601
    return ex


def _device(*, broadcast=(True, 30.0), channel_on=True, picture=None):
    """The Office TV: IP reads off, the Art channel announced and reads on.

    ``broadcast`` is (state, seconds ago).
    """
    now = time.monotonic()
    art_api = SimpleNamespace(
        art_mode=channel_on,
        art_mode_last_broadcast=(
            None if broadcast is None else (broadcast[0], now - broadcast[1])
        ),
    )
    device = object.__new__(SamsungTVDevice)
    device._entry_id = ENTRY
    device._host = "192.168.1.27"
    device._log = MagicMock()
    device._art_api = art_api
    device._ip_art_contradictions = 0
    device.async_write_ha_state = MagicMock()
    device.hass = SimpleNamespace(data={DOMAIN: {ENTRY: {DATA_ART_API: art_api}}})
    # For _art_mode_is_on: a Frame showing art reads OFF in Home Assistant.
    device._state = MediaPlayerState.OFF
    device._running_app = DEFAULT_APP
    device._rest_app_id = None
    device._ip_art_mode = False
    device._ip_art_mode_at = now
    device._ws = SimpleNamespace(artmode_status=ArtModeStatus.On)
    # REST says on (a 2024 Frame in art may say standby too), no SmartThings.
    device._device_info = {"device": {"PowerState": "on"}}
    device._device_info_at = now
    device._st = None
    snapshot = {"tv": {"pictureMode": "Intelligent"}, "powered_off": False}
    device._get_ip_control_state_coordinator = lambda: SimpleNamespace(data=snapshot)
    client = SimpleNamespace(
        async_get_picture_mode=AsyncMock(
            side_effect=_refused() if picture is None else None,
            return_value=picture,
        )
    )
    return device, client


async def _reads(device, client, n=mp.IP_ART_CONTRADICTION_READS, value=False):
    for _ in range(n):
        await device._check_ip_art_contradiction(
            client, value, "powerOn", time.monotonic()
        )


async def test_the_office_tv_is_read_from_the_art_channel_once_set_aside():
    device, client = _device()
    assert device._art_mode_is_on() is False  # the bug: IP wins past 20 s
    await _reads(device, client)
    assert device.hass.data[DOMAIN][ENTRY][DATA_IP_ART_UNRELIABLE] is True
    assert device._art_mode_is_on() is True
    assert device._ip_control_panel_art_cached() is None
    assert device._log.warning.call_count == 1
    await _reads(device, client)  # already set aside: no second warning
    assert device._log.warning.call_count == 1


async def test_it_takes_several_reads_in_a_row():
    device, client = _device()
    await _reads(device, client, n=mp.IP_ART_CONTRADICTION_READS - 1)
    assert not device._ip_art_reading_unreliable()
    client.async_get_picture_mode.assert_not_awaited()


async def test_a_read_that_agrees_resets_the_count():
    device, client = _device()
    await _reads(device, client, n=mp.IP_ART_CONTRADICTION_READS - 1)
    await _reads(device, client, n=1, value=True)
    await _reads(device, client, n=mp.IP_ART_CONTRADICTION_READS - 1)
    assert not device._ip_art_reading_unreliable()


async def test_a_real_picture_mode_means_the_broadcast_was_the_odd_one_out():
    # On a TV really showing an input, pictureModeControl answers its mode.
    device, client = _device(picture="Standard")
    await _reads(device, client)
    assert not device._ip_art_reading_unreliable()
    assert device._ip_art_contradictions == 0


async def test_ambient_from_picture_mode_control_also_confirms_art():
    device, client = _device(picture="Ambient")
    await _reads(device, client)
    assert device._ip_art_reading_unreliable()


async def test_no_answer_decides_nothing_yet():
    device, client = _device()
    client.async_get_picture_mode.side_effect = SamsungIPControlTransportError("x")
    await _reads(device, client)
    assert not device._ip_art_reading_unreliable()


@pytest.mark.parametrize(
    ("broadcast", "channel_on"),
    [
        ((True, 5.0), True),  # within the grace window: panel lag, not a fault
        ((False, 30.0), True),  # the TV last announced off
        (None, True),  # no broadcast at all: a frozen channel (#248)
        ((True, 30.0), False),  # the Art channel itself now reads off
    ],
)
async def test_only_a_contradiction_of_the_tv_s_own_announcement_counts(
    broadcast, channel_on
):
    device, client = _device(broadcast=broadcast, channel_on=channel_on)
    await _reads(device, client, n=mp.IP_ART_CONTRADICTION_READS + 2)
    assert not device._ip_art_reading_unreliable()


async def test_a_powered_off_tv_never_counts():
    device, client = _device()
    for _ in range(mp.IP_ART_CONTRADICTION_READS + 2):
        await device._check_ip_art_contradiction(
            client, False, "powerOff", time.monotonic()
        )
    assert not device._ip_art_reading_unreliable()


async def test_the_switch_stops_reading_the_panel_from_ip_control_too():
    # Its pre-write check and read-back use getTVStates, which does not show
    # art on this TV: once set aside, they report "unknown" and the switch
    # relies on the TV's broadcast, as it does without IP Control.
    from custom_components.samsungtv_smart.switch import FrameArtModeSwitch

    sw = object.__new__(FrameArtModeSwitch)
    client = SimpleNamespace(async_panel_shows_art=AsyncMock(return_value=False))
    sw._get_ip_control = lambda: client
    sw._entry = SimpleNamespace(entry_id=ENTRY)
    sw._hass = SimpleNamespace(data={DOMAIN: {ENTRY: {}}})
    assert await sw._panel_shows_art() is False
    sw._hass.data[DOMAIN][ENTRY][DATA_IP_ART_UNRELIABLE] = True
    assert await sw._panel_shows_art() is None
