"""directVolumeControl refused on an external output must not be re-read every poll.

Measured (192.168.1.161, LS03D feeding an AVR on HDMI3, 2026-10-05): from the
moment the sound went to the AVR (getTVStates: speakerSelect internal ->
external, volume 25 -> 0), every directVolumeControl read answered -32002 and
the TV's own log said "fail to get volume" (exact ms match on three reads). The
media player classified that as transient and asked again every 5 s — 44 times
in 4 minutes, until the end of the capture.

Some TVs do report an eARC soundbar's volume (see
test_ipcontrol_volume_media_player), so the refusal is remembered per output
rather than assumed for every external one: while the same output stays
selected the read is skipped (UPnP fallback as before); another output is asked
again.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

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
    SamsungIPControlError,
    SamsungIPControlModeLockedError,
)
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    SamsungTVDevice,
)
from homeassistant.components.media_player import (  # noqa: E402
    MediaPlayerEntityFeature,
    MediaPlayerState,
)

REFUSED = SamsungIPControlModeLockedError(
    "directVolumeControl returned error -32002: Server error"
)


def _device(output="CINEMA 60"):
    device = object.__new__(SamsungTVDevice)
    device._state = MediaPlayerState.ON
    device._attr_is_volume_muted = False
    device._attr_volume_level = 0.2
    device._st = None
    device._setvolumebyst = False
    device._ip_absolute_volume_supported = None
    device._ip_control_ambient_mode_active = MagicMock(return_value=False)
    device._log = MagicMock()
    device._upnp = AsyncMock()
    device._upnp.async_get_volume.return_value = 0
    device._upnp.async_get_mute.return_value = False
    device._power_off_in_progress = MagicMock(return_value=False)
    device._output = output
    device._speaker_output_state = lambda: device._output
    device._speaker_output_is_internal = lambda: (
        None if device._output is None else "internal" in device._output.lower()
    )
    client = AsyncMock()
    client.async_get_volume.side_effect = REFUSED
    device._get_ip_control_client = MagicMock(return_value=client)
    return device, client


async def test_replay_a_refusal_on_the_avr_is_not_asked_again_every_poll():
    device, client = _device()
    for _ in range(10):  # 50 s of polls
        await device._update_volume_info()
    assert client.async_get_volume.await_count == 1
    # UPnP still serves the volume, as before.
    assert device._upnp.async_get_volume.await_count == 10
    assert device._log.debug.call_count == 1


async def test_switching_back_to_the_internal_speakers_asks_again():
    device, client = _device()
    await device._update_volume_info()
    device._output = "Internal"
    client.async_get_volume.side_effect = None
    client.async_get_volume.return_value = 25
    await device._update_volume_info()
    assert client.async_get_volume.await_count == 2
    assert device._attr_volume_level == 0.25
    assert device._ip_volume_refused_output is None


async def test_another_external_output_is_asked_again():
    device, client = _device("CINEMA 60")
    await device._update_volume_info()
    device._output = "AudioOut/Optical"
    await device._update_volume_info()
    assert client.async_get_volume.await_count == 2
    assert device._ip_volume_refused_output == "AudioOut/Optical"


async def test_with_the_output_unknown_nothing_is_remembered():
    device, client = _device(output=None)
    await device._update_volume_info()
    await device._update_volume_info()
    assert client.async_get_volume.await_count == 2


async def test_a_refusal_on_the_internal_speakers_stays_transient():
    device, client = _device("Internal")
    await device._update_volume_info()
    await device._update_volume_info()
    assert client.async_get_volume.await_count == 2


async def test_other_ip_control_errors_stay_transient():
    device, client = _device()
    client.async_get_volume.side_effect = SamsungIPControlError("timeout")
    await device._update_volume_info()
    await device._update_volume_info()
    assert client.async_get_volume.await_count == 2


async def test_volume_set_is_hidden_while_refused_even_if_support_was_shown():
    device, _ = _device()
    await device._update_volume_info()
    # Support demonstrated earlier on the internal speakers…
    device._ip_absolute_volume_supported = True
    device._ip_volume_refused_output = "CINEMA 60"
    assert not device.supported_features & MediaPlayerEntityFeature.VOLUME_SET
    # …still holds once the internal speakers are back.
    device._output = "Internal"
    assert device.supported_features & MediaPlayerEntityFeature.VOLUME_SET
