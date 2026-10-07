"""Gate SmartThings background refresh on the locally known panel state.

SmartThings can keep reporting switch=on after the panel has powered down.
That stale cloud state must not keep sending refresh/refresh to a sleeping TV.

A Frame displaying Art Mode is different: Home Assistant reports its
media_player as OFF while the panel and network services are still active, so
Art Mode must continue to allow the periodic SmartThings refresh.

The gate therefore defers to ``_art_mode_is_on()`` rather than re-deriving the
panel state: that helper consults device_info ``PowerState`` only after the IP
Control reading, because a 2025 Frame reports ``standby`` while Art Mode is ON.
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
        "art_mode",
        "expected",
    ),
    [
        # Normal viewing.
        (MediaPlayerState.ON, False, False, True),
        # A power-off requested by HA must suppress refresh immediately: we
        # asked for standby, so stop before the cloud or any cache catches up.
        (MediaPlayerState.ON, True, False, False),
        # Frame Art Mode is HA OFF but the panel is still active.
        (MediaPlayerState.OFF, False, True, True),
        # Plain OFF with no evidence of Art Mode stays suppressed.
        (MediaPlayerState.OFF, False, False, False),
        # Nothing local knows yet: stay quiet. The 60 s throttle does not
        # advance on a skipped call, so the next informed poll refreshes at once.
        (MediaPlayerState.OFF, False, None, False),
    ],
)
def test_refresh_gate_distinguishes_a_sleeping_panel_from_an_active_one(
    media_state,
    power_off_in_progress,
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
            "_art_mode_is_on",
            return_value=art_mode,
        ),
    ):
        assert device._allow_st_refresh() is expected


def _panel_reporting_standby(ip_art_mode):
    """A panel whose device_info says standby, with an IP Control verdict.

    Only the attributes the real ``_art_mode_is_on()`` reads on this path are
    set, so the gate is exercised through it rather than around it.
    """
    device = object.__new__(SamsungTVDevice)
    device._state = MediaPlayerState.OFF  # a Frame showing art reads OFF in HA
    device._end_of_power_off = None
    device._device_info = {"device": {"PowerState": "standby"}}
    device._running_app = None
    device._ip_art_mode = ip_art_mode
    device._ip_art_mode_at = None
    device._latest_art_broadcast = lambda: None
    # No getTVStates coordinator registered: the cached-snapshot layer reads as
    # "cannot tell" and the lookup reaches device_info PowerState.
    device.hass = SimpleNamespace(data={})
    device._entry_id = "entry"
    return device


def test_powerstate_standby_does_not_outrank_a_frame_that_is_showing_art():
    """A 2025 Frame reports PowerState='standby' *while* Art Mode is ON.

    ``SamsungTVAsyncArt.in_artmode`` documents the quirk, and
    ``_art_mode_is_on()`` encodes the resulting priority: the IP Control
    reading is consulted before device_info. Reading PowerState in the gate
    itself would invert that and stop refreshing the very panel this gate
    exists to keep refreshing.
    """
    device = _panel_reporting_standby(ip_art_mode=True)

    assert device._art_mode_is_on() is True  # the panel is awake, showing art
    assert device._allow_st_refresh() is True  # ...so the gate must agree


def test_powerstate_standby_still_suppresses_a_genuinely_sleeping_panel():
    """With no art reading to outrank it, standby suppresses the refresh.

    This is the case the gate is for: SmartThings still reports switch=on, the
    panel is really asleep, and refresh/refresh would fail once a minute.
    """
    device = _panel_reporting_standby(ip_art_mode=None)

    assert device._art_mode_is_on() is False
    assert device._allow_st_refresh() is False


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
