"""A get_artmode_status answer must not undo a fresh art_mode_changed (#456).

A 2023 Frame (GQ55LS03B, local only, no IP Control) woken from standby by the
Art Mode switch:

    15:51:16.165  art_mode_changed  status=on
    15:51:16.167  set_artmode(on) confirmed early by an art_mode_changed broadcast
    15:51:16.170  Sent request 'get_artmode_status'
    15:51:16.207  get_artmode_status  value=off        <- stale, 40 ms later
    15:51:23.661  Frame Art: Using media_player art_mode_status: off
    ... off every 5 s for minutes, art on the panel

The stale reply overwrote the cached state the broadcast had just set, and
with nothing else reading the TV, that cache is art_mode_status.
"""

import json
import time
from unittest.mock import AsyncMock


def _d2d(art, inner: dict) -> tuple[str, dict]:
    return art.D2D_SERVICE_MESSAGE_EVENT, {
        "event": art.D2D_SERVICE_MESSAGE_EVENT,
        "data": json.dumps(inner),
    }


async def _broadcast(art_client, status: str) -> None:
    import art

    await art_client._process_event(
        *_d2d(art, {"event": "art_mode_changed", "status": status})
    )


async def _read_event(art_client, value: str) -> None:
    import art

    await art_client._process_event(
        *_d2d(art, {"event": "get_artmode_status", "value": value})
    )


async def test_a_stale_reply_right_after_the_broadcast_is_overruled(art_client):
    await _broadcast(art_client, "on")
    await _read_event(art_client, "off")
    assert art_client.art_mode is True


async def test_get_artmode_reports_the_broadcast_state_too(art_client):
    # The switch's fallback uses the returned value, not the cache.
    await _broadcast(art_client, "on")
    art_client._send_art_request = AsyncMock(
        return_value={"event": "get_artmode_status", "value": "off"}
    )
    assert await art_client.get_artmode() == "on"
    assert art_client.art_mode is True


async def test_the_same_holds_leaving_art_mode(art_client):
    await _broadcast(art_client, "off")
    await _read_event(art_client, "on")
    assert art_client.art_mode is False


async def test_a_read_that_agrees_with_the_broadcast_is_applied(art_client):
    await _broadcast(art_client, "on")
    art_client._send_art_request = AsyncMock(return_value={"value": "on"})
    assert await art_client.get_artmode() == "on"
    assert art_client.art_mode is True


async def test_past_the_window_the_read_is_trusted_again(art_client):
    # A broadcast the panel never followed must not pin the state.
    import art

    await _broadcast(art_client, "on")
    art_client.art_mode_last_broadcast = (
        True,
        time.monotonic() - art.ART_READ_BROADCAST_PRECEDENCE - 1,
    )
    await _read_event(art_client, "off")
    assert art_client.art_mode is False


async def test_without_any_broadcast_the_read_is_the_state(art_client):
    # Frames whose channel never receives art_mode_changed (#248) are unchanged.
    await _read_event(art_client, "on")
    assert art_client.art_mode is True
    await _read_event(art_client, "off")
    assert art_client.art_mode is False


async def test_a_newer_broadcast_still_wins_over_an_older_one(art_client):
    await _broadcast(art_client, "on")
    await _broadcast(art_client, "off")
    assert art_client.art_mode is False
    await _read_event(art_client, "on")
    assert art_client.art_mode is False
