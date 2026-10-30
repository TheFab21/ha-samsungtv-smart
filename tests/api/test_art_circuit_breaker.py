"""Circuit breaker: consecutive request timeouts must force a reconnect.

Covers the zombie-channel case from issue #153: the TV's art APP dies while
its network stack keeps the WebSocket open and answering PINGs, so the
heartbeat never fires, the receive loop never exits, and every request just
times out until the integration is reloaded.
"""

import asyncio


class _FakeWS:
    """Minimal live-looking WebSocket that records close() calls."""

    def __init__(self):
        self.closed = False
        self.close_calls = 0

    async def close(self):
        self.closed = True
        self.close_calls += 1


async def _timeout_once(art_client):
    """Run one request that times out immediately (nobody resolves the future)."""
    await art_client._wait_for_response("k", timeout=0)


async def test_breaker_trips_after_consecutive_timeouts(art_client):
    import art

    ws = _FakeWS()
    art_client._ws = ws
    art_client._connected = True

    for _ in range(art.ART_WS_TIMEOUT_TRIP):
        await _timeout_once(art_client)
    # The force-close runs in a task — let it execute.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert ws.close_calls == 1
    assert art_client._timeout_streak == 0  # reset after tripping
    assert art_client._request_cooldown_until > 0


async def test_success_resets_streak(art_client):
    import art

    ws = _FakeWS()
    art_client._ws = ws
    art_client._connected = True

    # Two timeouts (one below the trip threshold)...
    for _ in range(art.ART_WS_TIMEOUT_TRIP - 1):
        await _timeout_once(art_client)
    assert art_client._timeout_streak == art.ART_WS_TIMEOUT_TRIP - 1

    # ...then a real answer arrives: the streak must reset.
    fut = asyncio.get_event_loop().create_future()
    art_client._pending_requests["ok"] = fut
    fut.set_result({"event": "ok"})
    assert await art_client._wait_for_response("ok", timeout=1) == {"event": "ok"}
    assert art_client._timeout_streak == 0

    # A later single timeout starts again from 1 — no trip, no close.
    await _timeout_once(art_client)
    await asyncio.sleep(0)
    assert ws.close_calls == 0


async def test_no_trip_when_already_disconnected(art_client):
    import art

    art_client._ws = None
    art_client._connected = False

    for _ in range(art.ART_WS_TIMEOUT_TRIP + 1):
        await _timeout_once(art_client)
    await asyncio.sleep(0)

    # Nothing to close, no crash; the streak reset at the threshold (the
    # timeout right after it legitimately starts a fresh count at 1).
    assert art_client._timeout_streak < art.ART_WS_TIMEOUT_TRIP


async def test_art_requests_are_serialized(art_client, monkeypatch):
    """Shared HA entities must never issue overlapping Art requests."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    active = 0
    max_active = 0

    # Four positionals, as _send_art_request passes them: a stand-in that is
    # one short raises TypeError inside the task instead of running, so
    # first_started is never set and the test waits for ever rather than
    # failing. alias_keys came later than this test did.
    async def fake_send(_request, _wait_for_event, _timeout, _alias_keys=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        first_started.set()
        await release_first.wait()
        active -= 1
        return {"ok": True}

    monkeypatch.setattr(art_client, "_send_art_request_locked", fake_send)
    first = asyncio.create_task(art_client._send_art_request({"request": "one"}))
    await first_started.wait()
    second = asyncio.create_task(art_client._send_art_request({"request": "two"}))
    await asyncio.sleep(0)

    assert max_active == 1
    release_first.set()
    assert await first == {"ok": True}
    assert await second == {"ok": True}
    assert max_active == 1


async def test_recovery_cooldown_drops_requests(art_client, monkeypatch):
    """Pollers must not refill the dying socket while it reconnects."""
    import art

    called = False

    async def fake_send(_request, _wait_for_event, _timeout, _alias_keys=None):
        nonlocal called
        called = True

    monkeypatch.setattr(art_client, "_send_art_request_locked", fake_send)
    art_client._request_cooldown_until = (
        asyncio.get_running_loop().time() + art.ART_WS_RECOVERY_COOLDOWN
    )

    assert await art_client._send_art_request({"request": "poll"}) is None
    assert called is False


async def test_websocket_close_is_strictly_bounded(art_client, monkeypatch):
    """A half-open Samsung transport must not hang HA shutdown."""
    import art

    class _NeverClosingWS:
        async def close(self):
            await asyncio.Event().wait()

    monkeypatch.setattr(art, "ART_WS_CLOSE_TIMEOUT", 0.01)
    assert await art_client._close_ws_bounded(_NeverClosingWS()) is False
