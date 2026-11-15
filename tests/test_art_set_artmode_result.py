"""art_set_artmode / art_get_artmode must not report what the TV never said (#442).

On a 2024 Frame whose art channel never answered on either port,
art_set_artmode returned {"success": true} while the TV stayed on its home
screen: the service ignored set_artmode's own False. art_get_artmode returned
a bare {"status": null} with nothing to say the TV had not answered. What is
pinned here: success follows the TV's confirmation, a failure says whether the
request left Home Assistant at all, and a silent getter says so.
"""

import asyncio
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
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart import media_player as mp  # noqa: E402
from custom_components.samsungtv_smart.media_player import SamsungTVDevice  # noqa: E402


def _device(*, applied=True, sent=True, status="on"):
    device = object.__new__(SamsungTVDevice)
    device._log = MagicMock()
    device.stored = []
    device.exit_source = []

    async def frame():
        return True

    async def set_artmode(enabled):
        return applied

    device._ensure_frame_tv_check = frame
    device._art_write_guard = lambda: None
    device._art_api = SimpleNamespace(
        set_artmode=set_artmode,
        get_artmode=AsyncMock(return_value=status),
        last_set_artmode_sent=sent,
    )
    device._store_art_result = device.stored.append
    device._async_apply_art_exit_source = lambda: device.exit_source.append(1)
    device.hass = SimpleNamespace(async_create_task=lambda coro: None)
    return device


def _set(device, enabled):
    with patch.object(mp, "settle_after_source_switch", new=AsyncMock()):
        return asyncio.run(device.async_art_set_artmode(enabled))


def test_a_confirmed_change_is_a_success():
    result = _set(_device(applied=True), True)
    assert result["success"] is True
    assert "error" not in result


def test_an_unconfirmed_change_is_not_reported_as_success():
    # The #442 case: request sent, TV never answered, nothing on screen.
    result = _set(_device(applied=False, sent=True), True)
    assert result["success"] is False
    assert "did not confirm" in result["error"]


def test_a_request_that_never_left_says_the_channel_is_not_open():
    result = _set(_device(applied=False, sent=False), True)
    assert result["success"] is False
    assert "channel is not open" in result["error"]


def test_the_failure_is_what_the_frame_art_sensor_shows():
    device = _device(applied=False)
    result = _set(device, True)
    assert device.stored == [result]


def test_an_unconfirmed_exit_does_not_switch_the_exit_source():
    # The art exit source only follows a confirmed exit.
    device = _device(applied=False)
    _set(device, False)
    assert device.exit_source == []


def test_a_silent_getter_says_the_tv_did_not_answer():
    result = asyncio.run(_device(status=None).async_art_get_artmode())
    assert result["status"] is None
    assert "did not answer" in result["error"]


def test_an_answered_getter_has_no_error():
    result = asyncio.run(_device(status="off").async_art_get_artmode())
    assert result == {"service": "art_get_artmode", "status": "off"}
