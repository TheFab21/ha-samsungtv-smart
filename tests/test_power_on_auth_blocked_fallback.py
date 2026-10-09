"""A cold power-on must honour the configured wake method (#12/#273/#319).

Two bugs on the same path, one behaviour:

  * #12/#273 — on some ~2020 Frames the remote-control WebSocket can never
    authorize (the TV rejects the token on 8001 and the secure 8002 channel),
    tripping samsungws.auth_blocked. KEY_POWER then still reports "sent" while
    the TV ignores it with ms.channel.unauthorized, so the configured
    WOL/SmartThings/IP wake was never reached and the Frame stayed off.

  * #319 — on a 2022 Frame the remote channel reads `is_connected` in standby,
    so KEY_POWER reports "sent" over a live socket while the panel ignores it.
    The user had Power on method = SmartThings, yet the SmartThings wake never
    fired and the TV stayed off.

The cure for both: KEY_POWER over the WebSocket is never proof a Frame woke.
An explicitly configured SmartThings / IP Control method is honoured on every
cold power-on (it is idempotent on an already-waking set); WOL stays the
default and runs only when KEY_POWER could not be sent. These tests drive
_async_power_on with the WS and the wake transports mocked.
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
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart.api.ipcontrol import (  # noqa: E402
    SamsungIPControlError,
)
from custom_components.samsungtv_smart.api.samsungws import ArtModeStatus  # noqa: E402
from custom_components.samsungtv_smart.const import PowerOnMethod  # noqa: E402
from custom_components.samsungtv_smart.media_player import SamsungTVDevice  # noqa: E402
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402

_UNSET = object()


def _device(
    *,
    method=PowerOnMethod.WOL,
    key_power_sent=True,
    auth_blocked=False,
    is_connected=True,
    st=True,
    ip_client=_UNSET,
):
    """A Frame that is OFF and about to be told to power on.

    `key_power_sent` is what async_send_command(KEY_POWER) reports; `method`
    is the configured Power on method option.
    """
    device = object.__new__(SamsungTVDevice)
    device._log = MagicMock()
    device._host = "192.168.1.55"
    device._state = MediaPlayerState.OFF
    device._end_of_power_off = None

    device._ws = MagicMock()
    device._ws.artmode_status = ArtModeStatus.Unavailable
    device._ws.auth_blocked = auth_blocked
    device._ws.is_connected = is_connected
    device._ws.set_power_on_request = MagicMock()

    device.async_send_command = AsyncMock(return_value=key_power_sent)
    device._get_option = MagicMock(return_value=method.value)

    device._st = MagicMock() if st else None
    if device._st is not None:
        device._st.async_turn_on = AsyncMock()

    if ip_client is _UNSET:
        ip_client = MagicMock()
        ip_client.async_power_on = AsyncMock()
    device._get_ip_control_client = MagicMock(return_value=ip_client)

    device._send_wol_packet = MagicMock(return_value=True)
    device.hass = MagicMock()
    device.hass.async_add_executor_job = AsyncMock(return_value=True)
    return device


async def _wol_sent(device):
    """True if _send_wol_packet was handed to the executor."""
    return any(
        call.args and call.args[0] is device._send_wol_packet
        for call in device.hass.async_add_executor_job.await_args_list
    )


# --- #319: SmartThings / IP are honoured even when KEY_POWER "sent" ---------


async def test_smartthings_wakes_even_when_key_power_reported_sent():
    # The #319 Frame: KEY_POWER over a connected WS reports success, but the
    # panel stays asleep. The configured SmartThings wake must still fire.
    device = _device(method=PowerOnMethod.SmartThings, key_power_sent=True)

    assert await device._async_power_on() is True

    device.async_send_command.assert_awaited()  # KEY_POWER was still attempted
    device._st.async_turn_on.assert_awaited_once()
    assert not await _wol_sent(device)  # the default WOL never substitutes


async def test_ip_control_wakes_even_when_key_power_reported_sent():
    ip_client = MagicMock()
    ip_client.async_power_on = AsyncMock()
    device = _device(
        method=PowerOnMethod.IPControl, key_power_sent=True, ip_client=ip_client
    )

    assert await device._async_power_on() is True

    ip_client.async_power_on.assert_awaited_once()
    assert not await _wol_sent(device)


async def test_ip_control_falls_back_to_wol_when_the_client_refuses():
    ip_client = MagicMock()
    ip_client.async_power_on = AsyncMock(side_effect=SamsungIPControlError("410"))
    device = _device(
        method=PowerOnMethod.IPControl, key_power_sent=True, ip_client=ip_client
    )

    await device._async_power_on()

    ip_client.async_power_on.assert_awaited_once()
    assert await _wol_sent(device)  # a dead IP client still wakes the TV


# --- #12/#273: an auth-blocked channel still reaches the wake method --------


async def test_auth_blocked_skips_key_power_and_still_wakes_via_smartthings():
    device = _device(method=PowerOnMethod.SmartThings, auth_blocked=True)

    await device._async_power_on()

    device.async_send_command.assert_not_awaited()  # KEY_POWER skipped
    device._st.async_turn_on.assert_awaited_once()


async def test_auth_blocked_skips_key_power_and_still_wakes_via_wol():
    device = _device(method=PowerOnMethod.WOL, auth_blocked=True)

    await device._async_power_on()

    device.async_send_command.assert_not_awaited()
    assert await _wol_sent(device)


async def test_key_power_sent_over_a_dead_channel_counts_as_not_sent():
    # send_key() reports "sent" as soon as the frame is written; a channel that
    # is not connected cannot have woken anything, so WOL must still run.
    device = _device(method=PowerOnMethod.WOL, key_power_sent=True, is_connected=False)

    await device._async_power_on()

    assert await _wol_sent(device)


# --- WOL default is unchanged: a landed key over a live channel is enough ----


async def test_wol_default_does_not_fire_when_key_power_landed():
    device = _device(method=PowerOnMethod.WOL, key_power_sent=True)

    await device._async_power_on()

    device.async_send_command.assert_awaited()
    assert not await _wol_sent(device)  # KEY_POWER sufficed, no WOL


async def test_wol_fires_when_key_power_was_not_sent():
    device = _device(method=PowerOnMethod.WOL, key_power_sent=False)

    await device._async_power_on()

    assert await _wol_sent(device)
