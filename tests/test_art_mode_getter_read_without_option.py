"""The artModeControl getter is read whatever the IP Control art-mode option says.

The option ("Switch Art Mode over IP Control", off by default) now gates the
art-mode WRITES only. The documented damage (QE55LS03D fw 2123) came from
writes: before 8.7.7 the option stopped only the getter and users who turned it
off still had every toggle written over JSON-RPC. From 8.7.7 to 8.9.10 it
stopped the getter as well, so with the option off the IP art-mode cache stayed
None whenever the TV was on (measured on two Frames: "IP Control art-mode …
unchanged (None)" 1022 times in 78 min, no artModeControl in the TV's log).

A getter that wedges "on" is still caught by async_get_art_mode's cross-check
against getTVStates.pictureMode. The cached reading is stamped, and an
art_mode_changed broadcast outranks it for ART_BROADCAST_GRACE after the
broadcast, like the panel snapshot — except a powerOff reading, which nothing
overrides.
"""

from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    SamsungIPControl,
    SamsungIPControlUnsupportedError,
)
from custom_components.samsungtv_smart.api.samsungws import (  # noqa: E402
    ArtModeStatus,
)
from custom_components.samsungtv_smart.const import (  # noqa: E402
    DATA_ART_API,
    DEFAULT_APP,
    DOMAIN,
)
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    SamsungTVDevice,
)
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402

ENTRY_ID = "entry-161"
MEDIA_PLAYER = (
    Path(__file__).parents[1]
    / "custom_components"
    / "samsungtv_smart"
    / "media_player.py"
).read_text()


def _device(*, broadcast=None, power="powerOn", getter=True, frame=True):
    device = object.__new__(SamsungTVDevice)
    device._ws = SimpleNamespace(
        artmode_status=ArtModeStatus.On if frame else ArtModeStatus.Unsupported
    )
    device._device_info = None
    device._ip_control_ambient_mode_active = MagicMock(return_value=False)
    device._entry_id = ENTRY_ID
    device._host = "192.168.1.161"
    device._state = MediaPlayerState.ON
    device._running_app = DEFAULT_APP
    device._ip_art_mode = None
    device._ip_art_mode_failures = 0
    device._log = MagicMock()
    device._clear_ip_control_token_problem = MagicMock()
    device.async_write_ha_state = MagicMock()
    art_api = SimpleNamespace(art_mode_last_broadcast=broadcast, art_mode=None)
    device.hass = SimpleNamespace(data={DOMAIN: {ENTRY_ID: {DATA_ART_API: art_api}}})
    client = AsyncMock()
    client.async_get_power_state.return_value = power
    client.async_get_art_mode.return_value = getter
    device._get_ip_control_client = MagicMock(return_value=client)
    # The option must not matter: make it explicit that it is off.
    device._get_option = MagicMock(return_value=False)
    return device, client


async def _refresh(device, now=100.0):
    with patch(
        "custom_components.samsungtv_smart.media_player.time.monotonic",
        return_value=now,
    ):
        await device._refresh_ip_art_mode()


# ── The read ──────────────────────────────────────────────────────────────


async def test_the_getter_is_read_with_the_option_off():
    device, client = _device(getter=True)
    await _refresh(device, now=100.0)
    client.async_get_art_mode.assert_awaited_once_with(power_state="powerOn")
    assert device._ip_art_mode is True
    assert device._ip_art_mode_at == 100.0
    device.async_write_ha_state.assert_called_once()


async def test_power_is_asked_once_per_refresh():
    device, client = _device()
    await _refresh(device)
    client.async_get_power_state.assert_awaited_once()


async def test_a_powered_off_tv_is_not_asked_for_its_art_mode():
    device, client = _device(power="powerOff")
    await _refresh(device)
    client.async_get_art_mode.assert_not_awaited()
    assert device._ip_art_mode is False
    assert device._ip_art_mode_at is None  # definitive, never overridden


async def test_a_tv_without_art_mode_is_not_asked_for_it():
    # The refresh timer runs for every TV; a non-Frame has no artModeControl.
    device, client = _device(frame=False)
    await _refresh(device)
    client.async_get_art_mode.assert_not_awaited()
    assert device._ip_art_mode is None


async def test_a_frame_without_the_getter_is_asked_once():
    device, client = _device()
    client.async_get_art_mode.side_effect = SamsungIPControlUnsupportedError("-32601")
    await _refresh(device)
    await _refresh(device)
    assert client.async_get_art_mode.await_count == 1
    assert device._ip_art_mode is None
    assert device._ip_art_getter_supported is False


async def test_a_minus_32601_in_ambient_mode_is_not_latched():
    device, client = _device()
    device._ip_control_ambient_mode_active.return_value = True
    client.async_get_art_mode.side_effect = SamsungIPControlUnsupportedError("-32601")
    await _refresh(device)
    await _refresh(device)
    assert client.async_get_art_mode.await_count == 2
    assert device._ip_art_getter_supported is None


def test_the_read_path_no_longer_consults_the_option():
    begin = MEDIA_PLAYER.index("    async def _refresh_ip_art_mode")
    block = MEDIA_PLAYER[begin : MEDIA_PLAYER.index("    async def ", begin + 10)]
    assert "CONF_IP_CONTROL_ART_MODE, False" not in block
    assert "async_get_art_mode(power_state=power)" in block


async def test_ipcontrol_skips_its_own_power_read_when_given_one():
    client = object.__new__(SamsungIPControl)
    client._art_desync_count = 0
    requests = []

    async def _request(method, params=None):
        requests.append(method)
        if method == "artModeControl":
            return {"artMode": "artModeOn"}
        if method == "getTVStates":
            return {"pictureMode": "Ambient"}
        return {"power": "powerOn"}

    client._async_request = _request
    assert await client.async_get_art_mode(power_state="powerOn") is True
    assert requests == ["artModeControl", "getTVStates"]


# ── Arbitration of the cached reading ────────────────────────────────────


def test_a_getter_read_trailing_a_fresh_broadcast_is_overruled():
    # Broadcast ON at t=100; the getter, asked at t=103, still says off.
    device, _ = _device(broadcast=(True, 100.0))
    device._ip_art_mode = False
    device._ip_art_mode_at = 103.0
    assert device._art_mode_is_on() is True


def test_a_getter_read_past_the_grace_window_stands():
    device, _ = _device(broadcast=(True, 100.0))
    device._ip_art_mode = False
    device._ip_art_mode_at = 121.0
    assert device._art_mode_is_on() is False


def test_a_powered_off_reading_beats_any_broadcast():
    device, _ = _device(broadcast=(True, 100.0))
    device._ip_art_mode = False
    device._ip_art_mode_at = None
    assert device._art_mode_is_on() is False


async def test_an_unchanged_reading_that_retires_a_broadcast_is_published():
    # Cached False read during the grace window (the broadcast won); the same
    # False read again past it now stands — that outcome must be published.
    device, _ = _device(broadcast=(True, 100.0), getter=False)
    device._ip_art_mode = False
    device._ip_art_mode_at = 103.0
    await _refresh(device, now=125.0)
    device.async_write_ha_state.assert_called_once()
    assert device._art_mode_is_on() is False


async def test_an_unchanged_reading_with_no_disagreement_is_not_republished():
    device, _ = _device(broadcast=(True, 100.0), getter=True)
    device._ip_art_mode = True
    device._ip_art_mode_at = 103.0
    await _refresh(device, now=108.0)
    device.async_write_ha_state.assert_not_called()
