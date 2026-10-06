"""The art reconnect loop must survive a refused first port.

_connect_once cleaned up a failed attempt with close(), the intentional
teardown, which cancels the reconnect loop. Reached from that loop (receive
loop ended -> _reconnect_with_backoff -> open() -> _connect_once), it
cancelled itself: the CancelledError surfaced on the alternate port's
ws_connect and escaped ``except Exception``. 8.10.0 log, 192.168.1.161 after
it left the network:

    06:20:15.943 Art API: Connecting to ws://192.168.1.161:8001/...
    06:20:15.944 Art API: Port 8001 failed, trying alternate port 8002
    06:20:15.945 Art API: Connecting to wss://192.168.1.161:8002/...

and nothing more: no failure counted, no backoff, no "reconnect gave up".
"""

import asyncio
import json
import logging

import aiohttp


class _Session:
    """ws_connect refuses the ports in ``refused`` and accepts the others."""

    closed = False

    def __init__(self, refused):
        self.refused = set(refused)
        self.ports = []

    async def ws_connect(self, url, **_kwargs):
        port = int(url.split(":")[2].split("/")[0])
        self.ports.append(port)
        await asyncio.sleep(0)  # a real connect suspends here
        if port in self.refused:
            raise aiohttp.ClientConnectionError(f"Connection refused ({port})")
        return _WS()


class _WS:
    closed = False
    close_code = None

    def __init__(self):
        self._sent_connect = False
        self._forever = asyncio.Event()

    async def receive(self):
        self._sent_connect = True
        return aiohttp.WSMessage(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"event": "ms.channel.connect"}),
            None,
        )

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._forever.wait()
        raise StopAsyncIteration

    async def close(self):
        self.closed = True
        self._forever.set()
        return True

    async def ping(self):
        return None


async def _run_reconnect(art_client, session, monkeypatch, attempts):
    real_sleep = asyncio.sleep

    async def no_sleep(secs, *args):
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    art_client._external_session = session
    art_client._port = 8001
    task = asyncio.create_task(art_client._reconnect_with_backoff(attempts))
    art_client._reconnect_task = task  # as _receive_loop's finally sets it
    try:
        result = await task
    finally:
        monkeypatch.setattr(asyncio, "sleep", real_sleep)
    return task, result


async def test_both_ports_refused_is_counted_and_gives_up(
    art_client, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    session = _Session(refused={8001, 8002})

    task, result = await _run_reconnect(art_client, session, monkeypatch, 3)

    assert not task.cancelled()
    assert result is False
    assert session.ports[:2] == [8001, 8002]
    assert art_client._connection_failures == 1
    assert "Connection failure 1/3" in caplog.text
    assert "reconnect gave up after 3 attempts" in caplog.text


async def test_the_alternate_port_answering_reconnects(art_client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    session = _Session(refused={8001})

    task, result = await _run_reconnect(art_client, session, monkeypatch, 3)
    try:
        assert not task.cancelled()
        assert result is True
        assert session.ports == [8001, 8002]
        assert art_client._connected is True
        assert "reconnected after 1 attempt" in caplog.text
    finally:
        art_client._reconnect_task = None
        await art_client.close()


async def test_a_failed_lazy_open_leaves_a_sleeping_reconnect_loop_alone(
    art_client, monkeypatch
):
    # A poller's open() failing must not cancel the loop waiting to retry.
    sleeping = asyncio.create_task(asyncio.Event().wait())
    art_client._reconnect_task = sleeping
    art_client._external_session = _Session(refused={8001, 8002})
    art_client._port = 8001
    try:
        assert await art_client.open() is False
        await asyncio.sleep(0)
        assert not sleeping.done()
        assert art_client._reconnect_task is sleeping
    finally:
        sleeping.cancel()


async def test_the_end_of_the_receive_loop_is_logged(art_client, caplog):
    caplog.set_level(logging.DEBUG)
    ws = _WS()
    ws.close_code = 1000
    await ws.close()  # iteration ends at once, as aiohttp does on CLOSE
    art_client._ws = ws
    art_client._connected = True
    art_client._reconnect_task = asyncio.create_task(asyncio.sleep(0))
    await art_client._receive_loop()
    assert "receive loop ended (close code 1000)" in caplog.text
