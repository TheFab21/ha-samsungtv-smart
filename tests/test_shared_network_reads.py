import asyncio
from copy import deepcopy
import json

import pytest

from custom_components.samsungtv_smart.api.samsungws import SamsungTVAsyncRest
from custom_components.samsungtv_smart.api.shared_read import (
    _SESSION_POOLS,
    shared_json_get,
)
from custom_components.samsungtv_smart.api.upnp import SamsungUPnP


class Response:
    def __init__(self, session, body, status):
        self.session = session
        self.body = body
        self.status = status
        self.content = self

    async def __aenter__(self):
        self.session.started.set()
        try:
            await self.session.release.wait()
        except asyncio.CancelledError:
            self.session.cancelled += 1
            raise
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        self.session.json_reads += 1
        if self.session.error:
            raise self.session.error
        return self.body

    async def text(self):
        return json.dumps(self.body)

    async def read(self):
        return b"<root><CurrentVolume>37</CurrentVolume><CurrentMute>0</CurrentMute></root>"


class Session:
    def __init__(self, hold=False):
        self.calls = []
        self.body = {"nested": {"value": 1}}
        self.status = 200
        self.error = None
        self.json_reads = 0
        self.cancelled = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return Response(self, deepcopy(self.body), self.status)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return Response(self, deepcopy(self.body), self.status)

    put = post
    delete = post


async def test_pending_request_keys_do_not_expose_the_authorization_header():
    session = Session(hold=True)
    pending = asyncio.create_task(read_json(session, token="synthetic-credential"))
    await session.started.wait()
    keys = repr(_SESSION_POOLS[session]._reads)
    assert "synthetic-credential" not in keys
    assert "Bearer" not in keys
    session.release.set()
    await pending


async def read_json(session, url="https://api.example.test/status", token="synthetic"):
    async with shared_json_get(
        session, url, headers={"Authorization": "Bearer " + token}
    ) as response:
        return response.status, await response.json()


async def test_queued_cloud_reads_share_network_and_json_parsing():
    session = Session()
    results = await asyncio.gather(*(read_json(session) for _ in range(4)))
    assert len(session.calls) == session.json_reads == 1
    results[0][1]["nested"]["value"] = 99
    assert results[1][1]["nested"]["value"] == 1


async def test_completed_cloud_reads_are_not_cached():
    session = Session()
    assert (await read_json(session))[1]["nested"]["value"] == 1
    session.body["nested"]["value"] = 2
    assert (await read_json(session))[1]["nested"]["value"] == 2
    assert len(session.calls) == 2


async def test_an_already_running_cloud_read_cannot_answer_a_later_caller():
    session = Session(hold=True)
    old = asyncio.create_task(read_json(session))
    await session.started.wait()
    session.body["nested"]["value"] = 2
    new = asyncio.create_task(read_json(session))
    await asyncio.sleep(0)
    session.release.set()
    assert (await old)[1]["nested"]["value"] == 1
    assert (await new)[1]["nested"]["value"] == 2
    assert len(session.calls) == 2


@pytest.mark.parametrize("difference", ["token", "url", "session"])
async def test_cloud_contexts_are_kept_separate(difference):
    one = Session()
    two = Session() if difference == "session" else one
    await asyncio.gather(
        read_json(one),
        read_json(
            two,
            url=(
                "https://api.example.test/other"
                if difference == "url"
                else "https://api.example.test/status"
            ),
            token="other" if difference == "token" else "synthetic",
        ),
    )
    assert len(one.calls) + (len(two.calls) if two is not one else 0) == 2


async def test_http_failures_and_decode_errors_are_not_cached():
    session = Session()
    session.status = 403
    assert await read_json(session) == (403, None)
    assert session.json_reads == 0
    session.status = 200
    session.error = ValueError("synthetic invalid JSON")
    results = await asyncio.gather(
        read_json(session), read_json(session), return_exceptions=True
    )
    assert all(isinstance(result, ValueError) for result in results)
    session.error = None
    assert (await read_json(session))[0] == 200
    assert len(session.calls) == 3


async def test_a_shorter_waiter_timeout_does_not_cancel_another_consumer():
    session = Session(hold=True)
    first = asyncio.create_task(asyncio.wait_for(read_json(session), 0.01))
    second = asyncio.create_task(read_json(session))
    await session.started.wait()
    with pytest.raises(asyncio.TimeoutError):
        await first
    assert session.cancelled == 0
    session.release.set()
    assert (await second)[0] == 200
    assert len(session.calls) == 1


async def test_a_cloud_request_is_cancelled_when_nobody_needs_it():
    session = Session(hold=True)
    read = asyncio.create_task(read_json(session))
    await session.started.wait()
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    for _ in range(3):
        await asyncio.sleep(0)
    assert session.cancelled == 1


async def test_non_weak_referenceable_clients_keep_their_original_read_path():
    class Unhashable(Session):
        __hash__ = None

    session = Unhashable()
    await asyncio.gather(read_json(session), read_json(session))
    assert len(session.calls) == 2


async def test_tv_power_probes_share_only_queued_rest_reads():
    session = Session()
    session.body = {"device": {"PowerState": "on"}}
    clients = [SamsungTVAsyncRest("192.0.2.10", session) for _ in range(4)]
    results = await asyncio.gather(
        *(client.async_rest_device_info() for client in clients)
    )
    assert len(session.calls) == 1
    assert all(result["device"]["PowerState"] == "on" for result in results)
    session.body["device"]["PowerState"] = "off"
    assert (await clients[0].async_rest_device_info())["device"]["PowerState"] == "off"
    assert len(session.calls) == 2


async def test_tv_rest_writes_are_not_shared():
    session = Session()
    client = SamsungTVAsyncRest("192.0.2.10", session)
    await asyncio.gather(
        client._rest_request("applications/com.example.player", "POST"),
        client._rest_request("applications/com.example.player", "POST"),
    )
    assert len(session.calls) == 2


@pytest.mark.parametrize("method", ["async_get_volume", "async_get_mute"])
async def test_duplicate_upnp_reads_share_the_original_transport(method):
    session = Session()
    clients = [SamsungUPnP("192.0.2.10", session) for _ in range(4)]
    results = await asyncio.gather(*(getattr(client, method)() for client in clients))
    assert len(session.calls) == 1
    assert results == (["37"] * 4 if method == "async_get_volume" else [False] * 4)
    assert all(client.connected for client in clients)


async def test_upnp_setters_remain_independent():
    session = Session()
    client = SamsungUPnP("192.0.2.10", session)
    await asyncio.gather(client.async_set_volume(37), client.async_set_volume(37))
    assert len(session.calls) == 2


async def test_upnp_timeouts_close_the_network_request_and_recover():
    session = Session(hold=True)
    client = SamsungUPnP("192.0.2.10", session)
    assert (
        await client._soap_request("GetMute", "", "RenderingControl", timeout=0.01)
        is None
    )
    assert not client.connected
    assert session.cancelled == 1
    session.release.set()
    assert await client.async_get_mute() is False
    assert client.connected
