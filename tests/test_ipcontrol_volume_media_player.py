"""Tests for IP Control volume integration in the media player."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.media_player import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)

# The repository test requirements do not install pysmartthings.
# Provide the symbols needed while importing media_player.py.
if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        """Return arbitrary SmartThings enum-like attributes."""

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
    SamsungTVDevice,
)


def _device(*, external=True, muted=False, volume=0.20):
    """Create the minimum SamsungTVDevice needed by these unit tests."""
    device = object.__new__(SamsungTVDevice)

    device._state = MediaPlayerState.ON
    device._attr_is_volume_muted = muted
    device._attr_volume_level = volume

    device._st = None
    device._setvolumebyst = False

    device._ip_absolute_volume_supported = None
    device._ip_control_ambient_mode_active = MagicMock(return_value=False)
    device._latest_art_broadcast = MagicMock(return_value=None)
    device._ip_control_panel_art_cached = MagicMock(return_value=False)

    device._log = MagicMock()
    device._upnp = AsyncMock()

    device.async_send_command = AsyncMock()

    device._speaker_output_is_internal = MagicMock(
        return_value=not external
    )

    device._power_off_in_progress = MagicMock(
        return_value=False
    )

    return device


async def test_volume_poll_detects_direct_volume_control_support():
    """A successful local getter enables absolute-volume capability."""
    device = _device(external=True)

    client = AsyncMock()
    client.async_get_volume.return_value = 30

    device._get_ip_control_client = MagicMock(
        return_value=client
    )
    device._upnp.async_get_mute.return_value = False

    await device._update_volume_info()

    client.async_get_volume.assert_awaited_once_with()

    assert device._ip_absolute_volume_supported is True
    assert device._attr_volume_level == 0.30
    assert device._attr_is_volume_muted is False


async def test_unsupported_direct_volume_control_is_probed_only_once():
    """A confirmed -32601 disables future absolute-volume probes."""
    device = _device(external=True)

    client = AsyncMock()
    client.async_get_volume.side_effect = (
        SamsungIPControlUnsupportedError("Method not found")
    )

    device._get_ip_control_client = MagicMock(
        return_value=client
    )

    device._upnp.async_get_volume.return_value = 20
    device._upnp.async_get_mute.return_value = False

    await device._update_volume_info()
    await device._update_volume_info()

    assert device._ip_absolute_volume_supported is False

    # The optional capability is only probed once.
    assert client.async_get_volume.await_count == 1

    # Existing UPnP behaviour remains available.
    assert device._upnp.async_get_volume.await_count == 2
    assert device._attr_volume_level == 0.20


async def test_external_supported_volume_keeps_volume_set_feature():
    """External audio keeps the slider after local support is confirmed."""
    device = _device(external=True)

    device._ip_absolute_volume_supported = True

    features = device.supported_features

    assert features & MediaPlayerEntityFeature.VOLUME_SET


async def test_external_unsupported_volume_hides_volume_set_feature():
    """External audio retains the 8.7.0 slider protection if unsupported."""
    device = _device(external=True)

    device._ip_absolute_volume_supported = False

    features = device.supported_features

    assert not (
        features & MediaPlayerEntityFeature.VOLUME_SET
    )


async def test_external_absolute_volume_uses_ip_control():
    """Absolute volume for external audio is sent through IP Control."""
    device = _device(external=True, volume=0.20)

    client = AsyncMock()
    client.async_set_volume.return_value = 30

    device._get_ip_control_client = MagicMock(
        return_value=client
    )

    await device.async_set_volume_level(0.30)

    client.async_set_volume.assert_awaited_once_with(30)

    assert device._ip_absolute_volume_supported is True
    assert device._attr_volume_level == 0.30

    device._upnp.async_set_volume.assert_not_awaited()


async def test_external_unsupported_absolute_volume_is_not_sent_to_upnp():
    """External audio keeps the old protection after a -32601 response."""
    device = _device(external=True, volume=0.20)

    client = AsyncMock()
    client.async_set_volume.side_effect = (
        SamsungIPControlUnsupportedError("Method not found")
    )

    device._get_ip_control_client = MagicMock(
        return_value=client
    )

    await device.async_set_volume_level(0.30)

    client.async_set_volume.assert_awaited_once_with(30)

    assert device._ip_absolute_volume_supported is False

    # Never fall through to the ineffective external-speaker UPnP path.
    device._upnp.async_set_volume.assert_not_awaited()


async def test_external_speaker_mute_uses_key_mute_toggle():
    """External receiver/soundbar mute is relayed through KEY_MUTE."""
    device = _device(
        external=True,
        muted=False,
    )

    device._get_ip_control_client = MagicMock()

    await device.async_mute_volume(True)

    device.async_send_command.assert_awaited_once_with(
        "KEY_MUTE"
    )

    assert device._attr_is_volume_muted is True

    # Pressing the same HA mute button again requests unmute.
    await device.async_mute_volume(False)

    assert device.async_send_command.await_count == 2
    assert (
        device.async_send_command.await_args_list[1].args
        == ("KEY_MUTE",)
    )

    assert device._attr_is_volume_muted is False

    # External audio must not use IP Control muteControl.
    device._get_ip_control_client.assert_not_called()


async def test_internal_speaker_mute_uses_ip_control():
    """Internal speakers retain explicit IP Control mute state."""
    device = _device(
        external=False,
        muted=False,
    )

    client = AsyncMock()

    device._get_ip_control_client = MagicMock(
        return_value=client
    )

    await device.async_mute_volume(True)

    client.async_set_mute.assert_awaited_once_with(True)

    device.async_send_command.assert_not_awaited()

    assert device._attr_is_volume_muted is True


async def test_mute_does_nothing_when_state_already_matches():
    """No toggle is emitted when HA already has the requested mute state."""
    device = _device(
        external=True,
        muted=True,
    )

    await device.async_mute_volume(True)

    device.async_send_command.assert_not_awaited()


async def test_volume_is_not_read_over_ip_control_in_ambient_mode():
    """In Ambient mode directVolumeControl always answers -32601: don't ask.

    The method sits in the TV's none-ambient dispatch map; a QE32LS03C left in
    art answered 45 reads with -32601 in 4 min. Nothing is latched either, so
    volume control comes back as soon as the TV leaves Ambient mode.
    """
    device = _device(external=True)

    client = AsyncMock()
    client.async_get_volume.return_value = 30

    device._get_ip_control_client = MagicMock(
        return_value=client
    )
    device._ip_control_ambient_mode_active.return_value = True

    device._upnp.async_get_volume.return_value = 20
    device._upnp.async_get_mute.return_value = False

    await device._update_volume_info()

    assert device._ip_absolute_volume_supported is None
    assert device._attr_volume_level == 0.20
    client.async_get_volume.assert_not_awaited()

    # After leaving Ambient mode the method must be probed again and can
    # successfully establish support without reloading the integration.
    device._ip_control_ambient_mode_active.return_value = False

    await device._update_volume_info()

    assert device._ip_absolute_volume_supported is True
    assert device._attr_volume_level == 0.30
    assert client.async_get_volume.await_count == 1


async def test_a_minus_32601_read_while_the_snapshot_lags_ambient_is_not_latched():
    """The snapshot can say Ambient only after the read was refused.

    The read goes out (snapshot not yet Ambient), the TV answers -32601, and by
    the time the error is handled the snapshot reports Ambient: not latched.
    """
    device = _device(external=True)

    client = AsyncMock()
    client.async_get_volume.side_effect = SamsungIPControlUnsupportedError(
        "Method not found"
    )
    device._get_ip_control_client = MagicMock(return_value=client)
    device._ip_control_ambient_mode_active.side_effect = [False, True]
    device._upnp.async_get_volume.return_value = 20

    await device._update_volume_info()

    assert client.async_get_volume.await_count == 1
    assert device._ip_absolute_volume_supported is None


async def test_unsupported_volume_set_in_ambient_mode_is_not_latched():
    """A setter -32601 in Ambient mode must remain retryable."""
    device = _device(external=True, volume=0.20)

    client = AsyncMock()
    client.async_set_volume.side_effect = [
        SamsungIPControlUnsupportedError("Method not found"),
        30,
    ]

    device._get_ip_control_client = MagicMock(
        return_value=client
    )
    device._ip_control_ambient_mode_active.return_value = True

    await device.async_set_volume_level(0.30)

    assert device._ip_absolute_volume_supported is None
    assert device._attr_volume_level == 0.20
    assert client.async_set_volume.await_count == 1
    device._upnp.async_set_volume.assert_not_awaited()

    # Leaving Ambient mode must allow the same operation to succeed later.
    device._ip_control_ambient_mode_active.return_value = False

    await device.async_set_volume_level(0.30)

    assert device._ip_absolute_volume_supported is True
    assert device._attr_volume_level == 0.30
    assert client.async_set_volume.await_count == 2
    device._upnp.async_set_volume.assert_not_awaited()
