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

and nothing more: the loop was gone, with no trace of its outcome.

The loop's own attempts are not counted towards open()'s backoff, which
spaces the pollers: counted, three of them (at +1, +7 and +15 s) put open()
into a 2-minute backoff, so a TV back after that was refused by the loop's
later attempts and by any art write.
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


async def test_both_ports_refused_gives_up_without_arming_the_backoff(
    art_client, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    session = _Session(refused={8001, 8002})

    task, result = await _run_reconnect(art_client, session, monkeypatch, 3)

    assert not task.cancelled()
    assert result is False
    assert session.ports[:2] == [8001, 8002]
    assert art_client._connection_failures == 0
    assert art_client._backoff_until is None
    assert "Connection failure" not in caplog.text
    assert "reconnect gave up after 3 attempts" in caplog.text
    # The give-up is DEBUG, not WARNING. Scope to the art client's own records:
    # a bare caplog scan picks up warnings a concurrent test's task may emit.
    art_warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name.endswith(".art")
    ]
    assert not art_warnings


async def test_a_tv_back_after_46_s_is_reconnected_by_the_loop(art_client, monkeypatch):
    # 06:20 replay: the art socket drops as the TV leaves the network; the TV
    # answers again about 46 s later. Virtual clock: sleeps advance it.
    clock = [1000.0]
    start = clock[0]
    real_sleep = asyncio.sleep

    async def fake_sleep(secs, *args):
        clock[0] += secs
        await real_sleep(0)

    class _BackAt46(_Session):
        async def ws_connect(self, url, **kwargs):
            if clock[0] - start < 46:
                self.ports.append(url)
                await real_sleep(0)
                raise aiohttp.ClientConnectionError("Connection refused")
            return _WS()

    monkeypatch.setattr(art_module().time, "time", lambda: clock[0])
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    art_client._external_session = _BackAt46(refused=())
    art_client._port = 8001
    task = asyncio.create_task(art_client._reconnect_with_backoff())
    art_client._reconnect_task = task
    try:
        assert await task is True
        assert art_client._backoff_until is None
        assert art_client._connected is True
    finally:
        monkeypatch.setattr(asyncio, "sleep", real_sleep)
        art_client._reconnect_task = None
        await art_client.close()


def art_module():
    import art

    return art


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
