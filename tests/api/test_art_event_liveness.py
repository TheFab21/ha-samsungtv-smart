"""A 0.97 Frame that only speaks in broadcasts must not be torn down (#315).

The 2019 Frame (art API 0.97) keeps pushing ``art_mode_changed`` broadcasts
while never answering request/response reads. Those reads time out and used to
trip the wedge breaker, which force-closed the socket and backed off — and
during the backoff the broadcasts the Art Mode switch depends on were lost,
which is the multi-minute switch lag. An unsolicited event now proves the app
is alive and holds off the forced reconnect; a genuinely dead app (#153) pushes
nothing and still recovers.
"""

import json
import time

from unittest.mock import AsyncMock

import pytest


def _d2d(art, inner: dict) -> tuple[str, dict]:
    """Build the (event, response) pair _process_event expects."""
    return art.D2D_SERVICE_MESSAGE_EVENT, {
        "event": art.D2D_SERVICE_MESSAGE_EVENT,
        "data": json.dumps(inner),
    }


async def test_event_records_liveness_and_clears_the_streak(art_client):
    import art

    art_client._timeout_streak = 2
    art_client._got_response_since_connect = False
    art_client._last_event_at = None

    event, response = _d2d(art, {"event": "art_mode_changed", "status": "on"})
    await art_client._process_event(event, response)

    assert art_client._last_event_at is not None  # liveness stamped
    assert art_client._timeout_streak == 0  # streak cleared by the event
    assert art_client._got_response_since_connect is True  # connection productive
    assert art_client.art_mode is True  # broadcast still applied


def _arm_breaker(art_client, monkeypatch):
    """A connected client whose forced close is captured, not really run."""
    art_client._connected = True
    art_client._upload_in_progress = False
    art_client._timeout_streak = 0
    art_client._force_close_task = None
    art_client._ws = type("_WS", (), {"closed": False})()
    monkeypatch.setattr(art_client, "_force_close_ws", AsyncMock())


def _trip(art_client):
    import art

    for _ in range(art.ART_WS_TIMEOUT_TRIP):
        art_client._note_request_timeout()


async def test_recent_event_suppresses_the_forced_reconnect(art_client, monkeypatch):
    _arm_breaker(art_client, monkeypatch)
    art_client._last_event_at = time.monotonic()  # a broadcast just arrived

    _trip(art_client)

    assert art_client._force_close_task is None  # channel left connected
    assert art_client._timeout_streak == 0


async def test_no_event_still_forces_the_reconnect(art_client, monkeypatch):
    import art

    _arm_breaker(art_client, monkeypatch)
    art_client._last_event_at = None  # never heard from the app → zombie (#153)

    _trip(art_client)

    assert art_client._force_close_task is not None  # recovery still happens
    art_client._force_close_task.cancel()


async def test_stale_event_beyond_window_forces_the_reconnect(art_client, monkeypatch):
    import art

    _arm_breaker(art_client, monkeypatch)
    # Last event is older than the liveness window: the app went quiet, treat it
    # as possibly dead and recover.
    art_client._last_event_at = (
        time.monotonic() - art.ART_WS_EVENT_LIVENESS_WINDOW - 5
    )

    _trip(art_client)

    assert art_client._force_close_task is not None
    art_client._force_close_task.cancel()
