"""Gate SmartThings background refresh on the locally known panel state.

SmartThings can keep reporting switch=on after the panel has powered down.
That stale cloud state must not keep sending refresh/refresh to a sleeping TV.

A Frame displaying Art Mode is different: Home Assistant reports its
media_player as OFF while the panel and network services are still active, so
Art Mode must continue to allow the periodic SmartThings refresh.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

# The repository test requirements do not install pysmartthings.
# Other test modules may already have installed a partial stub depending on
# collection order, so augment the existing module instead of replacing it.
class _Names:
    """Return arbitrary SmartThings enum-like attributes."""

    def __getattr__(self, name):
        return name


pysmartthings = sys.modules.setdefault(
    "pysmartthings",
    ModuleType("pysmartthings"),
)
for _name in ("Attribute", "Capability", "Command"):
    if not hasattr(pysmartthings, _name):
        setattr(pysmartthings, _name, _Names())

if not hasattr(pysmartthings, "SmartThings"):
    pysmartthings.SmartThings = object


from custom_components.samsungtv_smart.api.smartthings import (  # noqa: E402
    STStatus,
    SmartThingsTV,
)
from custom_components.samsungtv_smart.media_player import (  # noqa: E402
    SamsungTVDevice,
)
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402


def _smartthings_client():
    """Build the minimum SmartThingsTV needed for async_device_update."""
    client = object.__new__(SmartThingsTV)
    client._api_key = "test-token"
    client._api_key_callback = None
    client._device_id = "test-device"
    client._state = STStatus.STATE_ON
    client._periodic_refresh = AsyncMock()
    client._st = SimpleNamespace(
        get_device_status=AsyncMock(return_value={}),
    )
    client._log = Mock()
    return client


@pytest.mark.parametrize(
    ("allow_refresh", "refresh_expected"),
    [
        (True, True),
        (False, False),
    ],
)
async def test_st_status_poll_continues_while_periodic_refresh_is_power_gated(
    allow_refresh,
    refresh_expected,
):
    """Suppress only refresh/refresh; never suppress the status read."""
    client = _smartthings_client()

    await SmartThingsTV.async_device_update.__wrapped__(
        client,
        allow_refresh=allow_refresh,
    )

    client._st.get_device_status.assert_awaited_once_with("test-device")

    if refresh_expected:
        client._periodic_refresh.assert_awaited_once_with()
    else:
        client._periodic_refresh.assert_not_awaited()


@pytest.mark.parametrize(
    (
        "media_state",
        "power_off_in_progress",
        "power_state",
        "art_mode",
        "expected",
    ),
    [
        # Normal viewing.
        (MediaPlayerState.ON, False, None, False, True),
        # A power-off requested by HA must suppress refresh immediately.
        (MediaPlayerState.ON, True, None, False, False),
        # Local device info confirming real standby wins over stale signals.
        (MediaPlayerState.OFF, False, "standby", True, False),
        # Frame Art Mode is HA OFF but the panel is still active.
        (MediaPlayerState.OFF, False, None, True, True),
        # Plain OFF with no evidence of Art Mode stays suppressed.
        (MediaPlayerState.OFF, False, None, False, False),
    ],
)
def test_refresh_gate_distinguishes_standby_from_art_mode(
    media_state,
    power_off_in_progress,
    power_state,
    art_mode,
    expected,
):
    """Distinguish a sleeping panel from an active Frame in Art Mode."""
    device = object.__new__(SamsungTVDevice)
    device._state = media_state

    with (
        patch.object(
            SamsungTVDevice,
            "_power_off_in_progress",
            return_value=power_off_in_progress,
        ),
        patch.object(
            SamsungTVDevice,
            "_get_device_spec",
            return_value=power_state,
        ),
        patch.object(
            SamsungTVDevice,
            "_art_mode_is_on",
            return_value=art_mode,
        ),
    ):
        assert device._allow_st_refresh() is expected


@pytest.mark.parametrize("allow_refresh", [True, False])
async def test_media_player_passes_panel_verdict_to_smartthings(allow_refresh):
    """Pass the already-resolved panel verdict into SmartThings."""
    st = SimpleNamespace(async_device_update=AsyncMock())

    device = object.__new__(SamsungTVDevice)
    device._st = st
    device._use_channel_info = True
    device._st_last_exc = None
    device._st_auth_error_count = 0

    with patch.object(
        SamsungTVDevice,
        "_allow_st_refresh",
        return_value=allow_refresh,
    ):
        assert await device._async_st_update() is True

    st.async_device_update.assert_awaited_once_with(
        True,
        allow_refresh=allow_refresh,
    )
