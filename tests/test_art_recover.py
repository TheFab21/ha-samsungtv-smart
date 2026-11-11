"""art_recover: one KEY_POWER to revive a wedged Art Mode channel (#12).

On some 2020 Frames the art app stops answering — the socket connects but
``ms.channel.ready`` never arrives — and nothing at the socket level revives it;
only a short KEY_POWER (or opening the app by hand) does. But KEY_POWER TOGGLES
Art Mode on a Frame, so pressing it while the TV is already in Art Mode turns
the panel off. What is pinned here is the guard: it acts only on a powered TV
that is not currently in Art Mode, never sends the key otherwise, reports
whether art came back, and rate-limits itself.
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

from custom_components.samsungtv_smart.media_player import SamsungTVDevice  # noqa: E402
from homeassistant.const import STATE_OFF, STATE_ON  # noqa: E402


class _ArtApi:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


def _device(*, frame=True, art_on=False, send_ok=True, recover_after_send=False):
    device = object.__new__(SamsungTVDevice)
    device._log = MagicMock()
    device.calls = []
    device._art_recover_cooldown_until = 0.0
    device._art_api = _ArtApi()
    device._art_now = art_on

    async def _frame_check():
        return frame

    device._ensure_frame_tv_check = _frame_check
    device._art_mode_is_on = lambda: device._art_now

    async def _send(payload, *a, **k):
        device.calls.append(("key", payload))
        if send_ok and recover_after_send:
            device._art_now = True  # the key press woke the art app
        return send_ok

    device.async_send_command = _send
    device._store_art_result = lambda result: device.calls.append(("stored", result))
    return device


def _run(device, state=STATE_ON, **kwargs):
    with patch.object(
        SamsungTVDevice, "state", new_callable=PropertyMock, return_value=state
    ):
        return asyncio.run(device.async_art_recover(**kwargs))


def _keys(device):
    return [c for c in device.calls if c[0] == "key"]


def test_a_non_frame_tv_is_refused_without_touching_the_tv():
    device = _device(frame=False)
    result = _run(device, delay=0)
    assert result["error"] == "Frame TV not supported"
    assert _keys(device) == []


def test_an_off_tv_is_refused_and_no_key_is_sent():
    # KEY_POWER on an off set would power it on — not this service's job.
    device = _device()
    result = _run(device, state=STATE_OFF, delay=0)
    assert "off" in result["error"]
    assert _keys(device) == []


def test_a_tv_already_in_art_mode_is_refused_the_footgun():
    # KEY_POWER while arting turns the panel off — the one thing to never do.
    device = _device(art_on=True)
    result = _run(device, delay=0)
    assert "already in Art Mode" in result["error"]
    assert _keys(device) == []
    assert "key_power_sent" not in result


def test_a_powered_tv_not_in_art_gets_one_key_press():
    device = _device()
    result = _run(device, delay=0)
    assert _keys(device) == [("key", "KEY_POWER")]
    assert result["key_power_sent"] is True


def test_the_stale_art_socket_is_dropped_after_the_press():
    device = _device()
    _run(device, delay=0)
    assert device._art_api.closed == 1


def test_recovery_is_reported_when_art_comes_back():
    device = _device(recover_after_send=True)
    result = _run(device, delay=0)
    assert result["recovered"] is True
    assert result["art_mode_on"] is True
    assert "note" not in result


def test_a_press_that_does_not_bring_art_back_says_so():
    device = _device(recover_after_send=False)
    result = _run(device, delay=0)
    assert result["recovered"] is False
    assert "note" in result


def test_a_control_channel_that_drops_the_key_is_reported():
    device = _device(send_ok=False)
    result = _run(device, delay=0)
    assert result["key_power_sent"] is False
    assert "error" in result
    assert "recovered" not in result
    assert device._art_api.closed == 0  # never touched the socket


def test_a_second_call_within_the_cooldown_is_refused():
    device = _device()
    _run(device, delay=0)
    assert _keys(device) == [("key", "KEY_POWER")]
    result2 = _run(device, delay=0)
    assert "moments ago" in result2["error"]
    assert _keys(device) == [("key", "KEY_POWER")]  # no second press


def test_the_outcome_is_published_to_the_frame_art_sensor():
    device = _device()
    result = _run(device, delay=0)
    assert ("stored", result) in device.calls


def test_the_service_is_registered_and_documented():
    from pathlib import Path

    root = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
    media_player = (root / "media_player.py").read_text()
    services = (root / "services.yaml").read_text()

    assert "SERVICE_ART_RECOVER," in media_player
    assert '"async_art_recover",' in media_player
    assert "\nart_recover:\n" in services
    # The power-toggle caveat must be where the user reads before calling it.
    assert "WARNING" in services and "turns the panel off" in services
