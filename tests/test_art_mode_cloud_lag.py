"""A lagging SmartThings reading must not unsee Art Mode (#12).

Measured on a 2020 Frame 32" with no IP Control paired:

    13:23:03  local power-on (KEY_POWER, method=WOL)
    13:23:08  SmartThings still reports STStatus.STATE_OFF
    13:23:10  art channel broadcasts art_mode_changed status='on'
    13:23:19→13:24:07  art_mode_status reads off, then the switch flips to off

The panel displayed art the whole time. `_art_mode_is_on()` weighs an
art_mode_changed broadcast against the IP Control snapshots and against
device_info PowerState, but the SmartThings STATE_OFF branch had no such
check — and on that TV it is the only branch that can return False (no IP
Control, PowerState reads 'on'). So the cloud's stale "off", taken two
seconds *before* the broadcast, outranked the TV's own announcement.

The broadcast now wins over a SmartThings reading older than it, with the
same ART_BROADCAST_GRACE bound as the other sources: past that the cloud is
the authority again, so a real power-off the art channel never witnessed is
still caught.

Also here: a Frame in Art Mode locks its picture mode, so writing one is
futile and used to burn the whole verify-and-fallback chain (two COMPLETED
answers that changed nothing, a 422, three warnings) on a TV behaving
normally.
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

from custom_components.samsungtv_smart.api.samsungws import ArtModeStatus  # noqa: E402
from custom_components.samsungtv_smart.api.smartthings import STStatus  # noqa: E402
from custom_components.samsungtv_smart.const import (  # noqa: E402
    DATA_ART_API,
    DEFAULT_APP,
    DOMAIN,
)
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    ART_BROADCAST_GRACE,
    SamsungTVDevice,
)
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402

ENTRY_ID = "entry-58"


def _device(*, st_state, broadcast, st_state_at=None, art_mode=True):
    """The reported TV: 2020 Frame, no IP Control, PowerState 'on', ST paired."""
    device = object.__new__(SamsungTVDevice)
    device._entry_id = ENTRY_ID
    device._host = "192.168.178.58"
    device._log = MagicMock()
    device._state = MediaPlayerState.OFF  # a Frame showing art reports OFF
    device._running_app = DEFAULT_APP
    device._ip_art_mode = None  # "not paired (no CONF_IP_CONTROL_TOKEN)"
    device._get_ip_control_state_coordinator = lambda: SimpleNamespace(data=None)
    # PowerState='on' throughout the measured window, so that branch is not
    # what produced the False.
    device._device_info = {"device": {"PowerState": "on"}}
    device._device_info_at = time.monotonic()
    device._st = SimpleNamespace(state=st_state, channel_name=None)
    device._st_state_at = time.monotonic() if st_state_at is None else st_state_at
    device._ws = SimpleNamespace(artmode_status=ArtModeStatus.Unsupported)
    # The sequence async_select_picture_mode stamps its writes with, as
    # __init__ sets it; the picture-mode tests below go through that path.
    device._picture_mode_seq = 0
    device._entry_data = {}
    device._ip_control_client = None
    device._ip_control_token_cached = None
    art_api = SimpleNamespace(art_mode=art_mode, art_mode_last_broadcast=broadcast)
    device.hass = SimpleNamespace(
        data={DOMAIN: {ENTRY_ID: {DATA_ART_API: art_api}}},
        # No IP Control on this TV: the entry lookup finds nothing, so no
        # token, which is what the picture-mode write walks through on its
        # way to SmartThings.
        config_entries=SimpleNamespace(async_get_entry=lambda _entry_id: None),
    )
    return device


# --- the cloud must not outrank a newer broadcast -------------------------


def test_a_broadcast_after_the_cloud_reading_wins():
    # The reported sequence: ST read at 13:23:08, broadcast at 13:23:10.
    now = time.monotonic()
    device = _device(
        st_state=STStatus.STATE_OFF,
        st_state_at=now - 2,
        broadcast=(True, now),
    )

    assert device._art_mode_is_on() is True


def test_the_cloud_regains_authority_once_the_broadcast_is_old():
    # A real power-off the art channel never witnessed must still be caught.
    now = time.monotonic()
    device = _device(
        st_state=STStatus.STATE_OFF,
        st_state_at=now,
        broadcast=(True, now - (ART_BROADCAST_GRACE + 5)),
    )

    assert device._art_mode_is_on() is False


def test_the_cloud_still_wins_with_no_broadcast_at_all():
    device = _device(st_state=STStatus.STATE_OFF, broadcast=None)

    assert device._art_mode_is_on() is False


def test_a_broadcast_saying_off_is_reported_as_off():
    # The override reports what the TV announced, not simply "on".
    now = time.monotonic()
    device = _device(
        st_state=STStatus.STATE_OFF,
        st_state_at=now - 2,
        broadcast=(False, now),
        art_mode=False,
    )

    assert device._art_mode_is_on() is False


def test_an_on_cloud_reading_is_untouched_and_falls_through():
    # Not STATE_OFF: the branch must not fire, and the art cache decides.
    device = _device(st_state=STStatus.STATE_ON, broadcast=None)

    assert device._art_mode_is_on() is True


# --- picture mode is locked while art is on -------------------------------


async def test_picture_mode_is_not_written_while_art_mode_is_on():
    device = _device(st_state=STStatus.STATE_ON, broadcast=None)
    device._st = MagicMock()
    device._st.async_set_picture_mode = AsyncMock(return_value=True)
    device.async_send_command = AsyncMock()
    device._art_mode_is_on = lambda: True

    await device.async_select_picture_mode("Dinamica")

    # Neither the cloud chain nor the WS key fallback runs.
    device._st.async_set_picture_mode.assert_not_awaited()
    device.async_send_command.assert_not_awaited()
    said = " ".join(str(c.args[0]) for c in device._log.info.call_args_list)
    assert "locks its picture mode while Art Mode is on" in said


async def test_picture_mode_is_written_normally_when_art_is_off():
    device = _device(st_state=STStatus.STATE_ON, broadcast=None)
    device._st = MagicMock()
    device._st.async_set_picture_mode = AsyncMock(return_value=True)
    device.async_send_command = AsyncMock()
    device._art_mode_is_on = lambda: False

    await device.async_select_picture_mode("Dinamica")

    device._st.async_set_picture_mode.assert_awaited_once_with("Dinamica")
    # A verified cloud apply sends no WS key on top.
    device.async_send_command.assert_not_awaited()


async def test_an_unknown_art_state_does_not_block_the_write():
    # _art_mode_is_on() returns None when nothing can tell; only a definite
    # True means the mode is locked.
    device = _device(st_state=STStatus.STATE_ON, broadcast=None)
    device._st = MagicMock()
    device._st.async_set_picture_mode = AsyncMock(return_value=True)
    device.async_send_command = AsyncMock()
    device._art_mode_is_on = lambda: None

    await device.async_select_picture_mode("Dinamica")

    device._st.async_set_picture_mode.assert_awaited_once_with("Dinamica")
