"""A fresh art_mode_changed broadcast must not lose to an older panel snapshot.

Measured on an LS03D (192.168.1.161, 2026-10-05, HA + on-TV logs): art was
switched on at 22:04:14.4 and the TV broadcast art_mode_changed='on', yet at
22:04:19.4 art_mode_status still read off. _art_mode_is_on() returned the
cached getTVStates.pictureMode first, and the last poll (10 s cadence) had been
taken while the TV, just woken, still showed CINEMA 60. Consequences in the
same log: "art_mode_status reads off but the panel shows Ambient — the reading
is stale" on a write that had just succeeded, and the Art Mode number entities
unavailable to a script 10 s after art came on.

The snapshot now carries when it was requested and the art client remembers
when the latest broadcast arrived. The broadcast wins over a reading taken
before it, and over one taken less than ART_BROADCAST_GRACE (20 s) after it:
the panel can trail the broadcast — a 2023 Frame's getTVStates had not reached
"Ambient" 11 s after it broadcast ON. Only broadcasts count: a
get_artmode_status reply can be the 13 h stale latch of #273, so it must never
outrank the panel.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.samsungtv_smart.api.art import (  # noqa: E402
    SamsungTVAsyncArt,
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
from custom_components.samsungtv_smart.sensor import (  # noqa: E402
    IPControlStateCoordinator,
)
from homeassistant.components.media_player import MediaPlayerState  # noqa: E402

ENTRY_ID = "entry-161"


def _device(*, picture_mode, polled_at, broadcast):
    """A Frame whose only readable signals are the panel snapshot and the art API."""
    device = object.__new__(SamsungTVDevice)
    device._entry_id = ENTRY_ID
    device._state = MediaPlayerState.OFF  # a Frame showing art reports OFF
    device._running_app = DEFAULT_APP
    device._ip_art_mode = None  # art-mode option off, as on the measured TV
    device._ws = SimpleNamespace(artmode_status=ArtModeStatus.On)
    snapshot = {"tv": {"pictureMode": picture_mode}, "powered_off": False}
    if polled_at is not None:
        snapshot["polled_at"] = polled_at
    device._get_ip_control_state_coordinator = lambda: SimpleNamespace(data=snapshot)
    art_api = SimpleNamespace(art_mode_last_broadcast=broadcast, art_mode=None)
    device.hass = SimpleNamespace(data={DOMAIN: {ENTRY_ID: {DATA_ART_API: art_api}}})
    return device


def test_replay_of_22_04_19_a_broadcast_after_the_snapshot_wins():
    # Snapshot requested at t=100 while the TV still showed its HDMI input;
    # the art_mode_changed='on' broadcast arrived at t=104.4.
    device = _device(picture_mode="Dynamic", polled_at=100.0, broadcast=(True, 104.4))
    assert device._art_mode_is_on() is True


def test_a_broadcast_older_than_the_snapshot_does_not_override_the_panel():
    # The #248 failure: a wedged art channel froze on 'off' long ago while the
    # panel moved to art. The panel, polled since, stays authoritative.
    device = _device(picture_mode="Ambient", polled_at=100.0, broadcast=(False, 40.0))
    assert device._art_mode_is_on() is True


def test_a_snapshot_shortly_after_the_broadcast_is_taken_for_panel_lag():
    # Broadcast ON at t=100, snapshot at t=105 not yet Ambient: the panel trails.
    device = _device(picture_mode="Dynamic", polled_at=105.0, broadcast=(True, 100.0))
    assert device._art_mode_is_on() is True


def test_a_snapshot_past_the_grace_window_is_the_authority_again():
    # Still not Ambient 25 s after the broadcast: a real conflict, panel wins.
    device = _device(picture_mode="Dynamic", polled_at=125.0, broadcast=(True, 100.0))
    assert device._art_mode_is_on() is False


def test_leaving_art_is_reported_from_the_broadcast_before_the_next_poll():
    device = _device(picture_mode="Ambient", polled_at=100.0, broadcast=(False, 101.5))
    assert device._art_mode_is_on() is False


def test_without_a_broadcast_the_panel_decides():
    device = _device(picture_mode="Dynamic", polled_at=100.0, broadcast=None)
    assert device._art_mode_is_on() is False


def test_a_snapshot_without_a_request_time_keeps_the_previous_behaviour():
    device = _device(picture_mode="Dynamic", polled_at=None, broadcast=(True, 104.4))
    assert device._art_mode_is_on() is False


def test_a_status_reply_is_not_a_broadcast():
    # art_mode True from a get_artmode_status reply but no broadcast: panel wins.
    device = _device(picture_mode="Dynamic", polled_at=100.0, broadcast=None)
    art_api = device.hass.data[DOMAIN][ENTRY_ID][DATA_ART_API]
    art_api.art_mode = True
    assert device._art_mode_is_on() is False


# ── The two timestamps ────────────────────────────────────────────────────


def _art_client():
    return SamsungTVAsyncArt(host="192.0.2.10", port=8002, token="tok", name="t")


def _event(sub_event, **fields):
    import json

    return {"data": json.dumps({"event": sub_event, **fields})}


async def test_art_mode_changed_records_state_and_arrival_time():
    art = _art_client()
    with patch(
        "custom_components.samsungtv_smart.api.art.time.monotonic", return_value=7.5
    ):
        await art._process_event(
            "d2d_service_message", _event("art_mode_changed", status="on")
        )
    assert art.art_mode_last_broadcast == (True, 7.5)


async def test_a_get_artmode_status_reply_leaves_the_broadcast_untouched():
    art = _art_client()
    await art._process_event(
        "d2d_service_message", _event("get_artmode_status", value="off")
    )
    assert art.art_mode is False
    assert art.art_mode_last_broadcast is None


async def test_the_snapshot_is_stamped_before_the_request_is_sent(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="TV", unique_id="tv-161", data={})
    entry.add_to_hass(hass)
    coordinator = IPControlStateCoordinator(hass, entry, "192.0.2.10")
    clock = iter([100.0, 200.0])
    client = AsyncMock()
    client.async_get_power_state.return_value = "powerOn"

    async def _states():
        # Any reading of the clock from here on is after the request left.
        return {"inputSource": "HDMI3", "pictureMode": "Dynamic"}

    client.async_get_tv_states.side_effect = _states
    with (
        patch.object(coordinator, "_get_ip_control", return_value=client),
        patch("custom_components.samsungtv_smart.sensor.clear_token_problem"),
        patch(
            "custom_components.samsungtv_smart.sensor.time.monotonic",
            side_effect=lambda: next(clock),
        ),
    ):
        data = await coordinator._async_update_data()
    assert data["polled_at"] == 100.0
