"""A -32601 at the moment the panel enters art is not a missing method.

directVolumeControl and the artModeControl getter sit in the TV's none-ambient
dispatch map: while the panel shows art they answer -32601 every time. The
code told that apart from "not on this model" with the getTVStates snapshot
alone, which is up to 10 s old and trails the art_mode_changed broadcast.

Measured on the 8.10.0 overnight log, 192.168.1.161 (LS03D) woken into art:

    05:00:04.171 IP Control absolute volume detected for this TV
    05:00:06.346 art_mode_changed on
    05:00:06.830 IP Control absolute volume is not available on this TV: -32601

and no IP Control volume read for the remaining six hours, through four
stretches of normal viewing. Now a fresh broadcast ON counts, and a capability
the TV has already shown is not taken away by one refusal.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.media_player import MediaPlayerState

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart.api.ipcontrol import (  # noqa: E402
    SamsungIPControlUnsupportedError,
)
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    ArtModeSupport,
    SamsungTVDevice,
)

MONOTONIC = "custom_components.samsungtv_smart.media_player.time.monotonic"
REFUSED = SamsungIPControlUnsupportedError("-32601 Method not found")


def _device(*, supported=None, broadcast=None, ambient=False):
    device = object.__new__(SamsungTVDevice)
    device._state = MediaPlayerState.ON
    device._attr_is_volume_muted = False
    device._attr_volume_level = 0.2
    device._st = None
    device._setvolumebyst = False
    device._ip_absolute_volume_supported = supported
    device._ip_volume_refused_output = None
    device._ip_control_ambient_mode_active = MagicMock(return_value=ambient)
    device._latest_art_broadcast = MagicMock(return_value=broadcast)
    device._log = MagicMock()
    device._upnp = AsyncMock()
    device._upnp.async_get_volume.return_value = 20
    device._upnp.async_get_mute.return_value = False
    device._power_off_in_progress = MagicMock(return_value=False)
    device._speaker_output_state = MagicMock(return_value="Internal")
    device._speaker_output_is_internal = MagicMock(return_value=True)
    client = AsyncMock()
    device._get_ip_control_client = MagicMock(return_value=client)
    return device, client


async def _update(device, now):
    with patch(MONOTONIC, return_value=now):
        await device._update_volume_info()


# ── Volume read ──────────────────────────────────────────────────────────────


async def test_the_overnight_sequence_keeps_absolute_volume():
    device, client = _device()
    client.async_get_volume.return_value = 30
    await _update(device, now=4.171)  # detected
    assert device._ip_absolute_volume_supported is True

    # art_mode_changed on at 6.346 arrives just after the update started
    # (snapshot still a real picture mode): the read is refused.
    client.async_get_volume.side_effect = REFUSED
    await _update(device, now=6.830)
    assert device._ip_absolute_volume_supported is True

    # Normal viewing later: asked again over IP Control.
    client.async_get_volume.side_effect = None
    client.async_get_volume.return_value = 25
    await _update(device, now=100.0)
    assert client.async_get_volume.await_count == 3
    assert device._attr_volume_level == 0.25


async def test_a_fresh_broadcast_on_skips_the_read():
    # Broadcast ON 0.5 s ago, snapshot not Ambient yet: don't ask.
    device, client = _device(broadcast=(True, 100.0))
    await _update(device, now=100.5)
    client.async_get_volume.assert_not_awaited()
    assert device._ip_absolute_volume_supported is None
    device._upnp.async_get_volume.assert_awaited_once()


async def test_a_refusal_during_a_fresh_broadcast_is_not_latched():
    device, _ = _device(broadcast=(True, 100.0))
    with patch(MONOTONIC, return_value=103.0):
        device._note_ip_volume_unsupported(REFUSED)
    assert device._ip_absolute_volume_supported is None


async def test_a_broadcast_past_the_grace_window_no_longer_counts():
    device, client = _device(broadcast=(True, 100.0))
    client.async_get_volume.side_effect = REFUSED
    await _update(device, now=121.0)
    client.async_get_volume.assert_awaited_once()
    assert device._ip_absolute_volume_supported is False


async def test_a_broadcast_off_does_not_count():
    device, client = _device(broadcast=(False, 100.0))
    client.async_get_volume.side_effect = REFUSED
    await _update(device, now=101.0)
    assert device._ip_absolute_volume_supported is False


async def test_a_tv_that_never_answered_is_still_ruled_out_once():
    device, client = _device()
    client.async_get_volume.side_effect = REFUSED
    await _update(device, now=10.0)
    await _update(device, now=20.0)
    assert device._ip_absolute_volume_supported is False
    client.async_get_volume.assert_awaited_once()


# ── Volume set ───────────────────────────────────────────────────────────────


async def test_a_refused_set_keeps_a_proven_capability():
    device, client = _device(supported=True)
    client.async_set_volume.side_effect = REFUSED
    with patch(MONOTONIC, return_value=50.0):
        await device.async_set_volume_level(0.3)
    assert device._ip_absolute_volume_supported is True


async def test_a_refused_set_during_a_fresh_broadcast_is_not_latched():
    device, client = _device(broadcast=(True, 50.0))
    client.async_set_volume.side_effect = REFUSED
    with patch(MONOTONIC, return_value=52.0):
        await device.async_set_volume_level(0.3)
    assert device._ip_absolute_volume_supported is None


# ── Art-mode getter (same pattern, new in 8.10.0) ────────────────────────────


def _getter_device(*, supported=None, broadcast=None):
    device, _ = _device(broadcast=broadcast)
    device._host = "192.168.1.161"
    device._ip_art_mode = None
    device._ip_art_mode_at = None
    device._ip_art_mode_failures = 0
    device._ip_art_getter_supported = supported
    device._clear_ip_control_token_problem = MagicMock()
    device.async_write_ha_state = MagicMock()
    client = AsyncMock()
    client.async_get_power_state.return_value = "powerOn"
    client.async_get_art_mode.side_effect = REFUSED
    device._get_ip_control_client = MagicMock(return_value=client)
    return device, client


async def _refresh(device, now):
    with (
        patch(MONOTONIC, return_value=now),
        patch.object(
            SamsungTVDevice,
            "support_art_mode",
            new=property(lambda self: ArtModeSupport.FULL),
        ),
    ):
        await device._refresh_ip_art_mode()


async def test_a_getter_that_has_answered_is_not_latched_by_one_refusal():
    device, client = _getter_device(supported=True)
    await _refresh(device, now=10.0)
    await _refresh(device, now=15.0)
    assert client.async_get_art_mode.await_count == 2
    assert device._ip_art_getter_supported is True


async def test_a_getter_refusal_during_a_fresh_broadcast_is_not_latched():
    device, client = _getter_device(broadcast=(True, 10.0))
    await _refresh(device, now=10.5)
    assert device._ip_art_getter_supported is None


async def test_a_successful_getter_read_marks_it_supported():
    device, client = _getter_device()
    client.async_get_art_mode.side_effect = None
    client.async_get_art_mode.return_value = False
    await _refresh(device, now=10.0)
    assert device._ip_art_getter_supported is True


async def test_a_getter_that_never_answered_is_still_ruled_out():
    device, client = _getter_device()
    await _refresh(device, now=10.0)
    await _refresh(device, now=15.0)
    assert client.async_get_art_mode.await_count == 1
    assert device._ip_art_getter_supported is False
