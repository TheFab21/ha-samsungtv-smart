"""A Hue Sync command SmartThings refuses must give a readable error (#298).

The reporter of #298 got "Unknown error" in Home Assistant and only
"409, message='Conflict'" in the log: any refusal but a 422 escaped
_async_set_hue_sync as a raw ClientResponseError. It now becomes a
HomeAssistantError carrying SmartThings' reason, and a 409 also reads and logs
the TV's SmartThings health, the first thing to tell apart (unreachable TV vs a
refusal of this command).

Also: on a Frame 2024 that idles at supportedModes [""], start_hue_sync now
launches the app instead of treating the placeholder as a running session.
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

from aiohttp import ClientResponseError  # noqa: E402
import pytest  # noqa: E402

from custom_components.samsungtv_smart.api.smartthings import (  # noqa: E402
    SmartThingsCapabilityUnsupported,
)
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    SamsungTVDevice,
)
from homeassistant.exceptions import HomeAssistantError  # noqa: E402


def _device(*, session_active=True, refusal=None, health="ONLINE"):
    device = object.__new__(SamsungTVDevice)
    device._log = MagicMock()
    device._st = MagicMock()
    device._st.async_hue_sync_session_active = AsyncMock(return_value=session_active)
    device._st.async_set_hue_sync = AsyncMock(side_effect=refusal)
    device._st.async_device_health = AsyncMock(return_value=health)
    device._async_launch_hue_sync_app = AsyncMock()
    return device


def _refusal(status, message):
    return ClientResponseError(MagicMock(), (), status=status, message=message)


async def test_a_409_is_a_readable_error_with_the_tv_health():
    device = _device(
        refusal=_refusal(409, "Conflict (ConflictError: invalid device state)"),
        health="OFFLINE",
    )

    with pytest.raises(HomeAssistantError) as caught:
        await device.async_stop_hue_sync()

    text = str(caught.value)
    assert "refused to stop Hue Sync" in text
    assert "HTTP 409" in text
    assert "ConflictError: invalid device state" in text
    assert "OFFLINE" in text
    device._st.async_device_health.assert_awaited_once()
    device._log.warning.assert_called_once()


async def test_other_refusals_are_readable_without_a_health_read():
    device = _device(refusal=_refusal(403, "Forbidden"))

    with pytest.raises(HomeAssistantError) as caught:
        await device.async_start_hue_sync()

    assert "refused to start Hue Sync" in str(caught.value)
    assert "HTTP 403: Forbidden" in str(caught.value)
    device._st.async_device_health.assert_not_awaited()


async def test_a_missing_capability_keeps_its_own_message():
    device = _device(refusal=SmartThingsCapabilityUnsupported("samsungvd.lightControl"))

    with pytest.raises(HomeAssistantError) as caught:
        await device.async_start_hue_sync()

    assert "does not currently expose" in str(caught.value)


async def test_no_session_launches_the_app_before_starting():
    # What a Frame 2024 idling at supportedModes [""] now reads as (False).
    device = _device(session_active=False)

    await device.async_start_hue_sync()

    device._async_launch_hue_sync_app.assert_awaited_once()
    device._st.async_set_hue_sync.assert_awaited_once_with(True)


async def test_no_session_means_nothing_to_stop():
    device = _device(session_active=False)

    with pytest.raises(HomeAssistantError) as caught:
        await device.async_stop_hue_sync()

    assert "nothing to stop" in str(caught.value)
    device._st.async_set_hue_sync.assert_not_awaited()
