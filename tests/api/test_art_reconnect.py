"""Auto-reconnect: a closed WS mid-session must trigger a backed-off reconnect."""


def test_backoff_delay_grows_and_caps(art_client):
    import art

    assert art.SamsungTVAsyncArt._backoff_delay(0) == 1.0
    assert art.SamsungTVAsyncArt._backoff_delay(1) == 2.0
    assert art.SamsungTVAsyncArt._backoff_delay(2) == 4.0
    # capped at 60s no matter how high the attempt count
    assert art.SamsungTVAsyncArt._backoff_delay(20) == 60.0


async def test_receive_loop_reconnects_on_close(art_client, monkeypatch):
    reopened = {"count": 0}

    async def fake_open(**_kwargs):
        reopened["count"] += 1
        art_client._connected = True
        return True

    async def no_sleep(_secs):
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(art_client, "open", fake_open)

    # Simulate: loop body observed a closed socket once, then we stop.
    art_client._connected = True
    await art_client._reconnect_with_backoff(max_attempts=1)

    assert reopened["count"] == 1
