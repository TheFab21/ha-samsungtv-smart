"""Tests for optional local tuner channel polling."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

# The repository test requirements do not install pysmartthings.
# Provide the symbols required while importing sensor.py.
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
    SamsungIPControlUnsupportedError,
)
from custom_components.samsungtv_smart.const import (  # noqa: E402
    CONF_IS_FRAME_TV,
    DOMAIN,
)
from custom_components.samsungtv_smart.sensor import (  # noqa: E402
    IPControlStateCoordinator,
)

HOST = "192.0.2.10"


def _entry(hass, *, is_frame=False):
    """Create a minimal TV config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Samsung TV",
        unique_id="test-samsung-tv",
        data={
            CONF_IS_FRAME_TV: is_frame,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _client(*, source="TV"):
    """Create a fake IP Control client."""
    client = AsyncMock()
    client.async_get_power_state.return_value = "powerOn"
    client.async_get_tv_states.return_value = {
        "inputSource": source,
    }
    client.async_get_channel.return_value = {
        "atvDtv": "dtv",
        "airCable": "air",
        "channelNum": "5",
    }
    return client


async def _update(coordinator, client):
    """Run one coordinator update using the fake client."""
    with (
        patch.object(
            coordinator,
            "_get_ip_control",
            return_value=client,
        ),
        patch("custom_components.samsungtv_smart.sensor.clear_token_problem"),
    ):
        return await coordinator._async_update_data()


async def test_tuner_channel_is_added_for_non_frame_tv(hass):
    """A non-Frame TV on its tuner exposes local channel metadata."""
    entry = _entry(hass)
    coordinator = IPControlStateCoordinator(hass, entry, HOST)
    client = _client(source="TV")

    data = await _update(coordinator, client)

    # When the snapshot was requested, for the media player's freshness check.
    assert isinstance(data.pop("polled_at"), float)
    assert data == {
        "tv": {"inputSource": "TV"},
        "channel": {
            "atvDtv": "dtv",
            "airCable": "air",
            "channelNum": "5",
        },
        "powered_off": False,
    }
    assert coordinator._channel_control_supported is True
    client.async_get_channel.assert_awaited_once_with()


async def test_frame_tv_on_tuner_queries_channel_control(hass):
    """A Frame TV has a tuner too, so it is queried like any other TV.

    Panels that do not implement directChannelControl answer -32601 and are
    then never asked again, which is what
    test_unsupported_channel_control_is_probed_only_once covers.
    """
    entry = _entry(hass, is_frame=True)
    coordinator = IPControlStateCoordinator(hass, entry, HOST)
    client = _client(source="TV")

    data = await _update(coordinator, client)

    assert data["tv"] == {"inputSource": "TV"}
    assert data["channel"]["channelNum"] == "5"
    assert data["powered_off"] is False
    assert coordinator._channel_control_supported is True
    client.async_get_channel.assert_awaited_once_with()


async def test_hdmi_never_queries_channel_control(hass):
    """A non-tuner input never triggers a channel metadata query."""
    entry = _entry(hass)
    coordinator = IPControlStateCoordinator(hass, entry, HOST)
    client = _client(source="HDMI1")

    data = await _update(coordinator, client)

    assert data["tv"] == {"inputSource": "HDMI1"}
    assert data["channel"] == {}
    assert data["powered_off"] is False
    assert coordinator._channel_control_supported is None
    client.async_get_channel.assert_not_awaited()


async def test_unsupported_channel_control_is_probed_only_once(hass):
    """A confirmed -32601 prevents repeated capability probes."""
    entry = _entry(hass)
    coordinator = IPControlStateCoordinator(hass, entry, HOST)
    client = _client(source="TV")
    client.async_get_channel.side_effect = SamsungIPControlUnsupportedError(
        "Method not found"
    )

    first = await _update(coordinator, client)
    second = await _update(coordinator, client)

    assert first["tv"] == {"inputSource": "TV"}
    assert first["channel"] == {}
    assert first["powered_off"] is False

    assert second["tv"] == {"inputSource": "TV"}
    assert second["channel"] == {}
    assert second["powered_off"] is False

    assert coordinator._channel_control_supported is False

    # Only the first poll probes directChannelControl.
    assert client.async_get_channel.await_count == 1
    assert client.async_get_tv_states.await_count == 2


async def test_transient_channel_error_does_not_disable_capability(hass):
    """A transient channel read failure does not invalidate the TV snapshot."""
    entry = _entry(hass)
    coordinator = IPControlStateCoordinator(hass, entry, HOST)
    client = _client(source="TV")

    client.async_get_channel.side_effect = SamsungIPControlError(
        "Temporary channel read failure"
    )

    first = await _update(coordinator, client)

    assert isinstance(first.pop("polled_at"), float)
    assert first == {
        "tv": {"inputSource": "TV"},
        "channel": {},
        "powered_off": False,
    }
    assert coordinator._channel_control_supported is None
    client.async_get_channel.assert_awaited_once_with()

    # A later poll must be allowed to retry the optional capability.
    client.async_get_channel.side_effect = None
    client.async_get_channel.return_value = {
        "atvDtv": "dtv",
        "airCable": "air",
        "channelNum": "7",
    }

    second = await _update(coordinator, client)

    assert second["channel"]["channelNum"] == "7"
    assert coordinator._channel_control_supported is True
    assert client.async_get_channel.await_count == 2


async def test_art_mode_is_never_polled_for_a_channel(hass):
    """Art mode still reports inputSource "TV", but has no channel.

    Measured on a Frame: getTVStates returns inputSource "TV" and pictureMode
    "Ambient" while art is displayed, and directChannelControl answered a
    channel once and -32601 seconds later. Polling it there would be both
    meaningless and unbounded.
    """
    entry = _entry(hass, is_frame=True)
    coordinator = IPControlStateCoordinator(hass, entry, HOST)
    client = _client(source="TV")
    client.async_get_tv_states.return_value = {
        "inputSource": "TV",
        "pictureMode": "Ambient",
    }

    first = await _update(coordinator, client)

    assert first["channel"] == {}
    assert coordinator._channel_control_supported is None
    client.async_get_channel.assert_not_awaited()

    # Leaving art mode must let the channel work again without a restart.
    client.async_get_tv_states.return_value = {
        "inputSource": "TV",
        "pictureMode": "Standard",
    }
    client.async_get_channel.return_value = {"channelNum": "1001"}

    second = await _update(coordinator, client)

    assert second["channel"]["channelNum"] == "1001"
    assert coordinator._channel_control_supported is True
    assert client.async_get_channel.await_count == 1
