import asyncio

import pytest

from custom_components.samsungtv_smart.api.ipcontrol import (
    _HOST_LOCKS,
    SamsungIPControl,
    SamsungIPControlError,
    priority_requests,
)


class Backend:
    def __init__(self, *, hold=False):
        self.calls = []
        self.volume = 10
        self.error = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not hold:
            self.release.set()

    async def async_add_executor_job(self, function, method, params, *args):
        self.calls.append((method, params))
        observed_volume = self.volume
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error
        if params and "volume" in params:
            self.volume = params["volume"]
            observed_volume = self.volume
        return {"volume": observed_volume, "nested": {"value": "original"}}


@pytest.fixture
def backend():
    _HOST_LOCKS.clear()
    yield Backend()
    _HOST_LOCKS.clear()


def client(backend, *, token="synthetic", port=1516):
    return SamsungIPControl(backend, "192.0.2.10", token=token, port=port)


async def settle():
    for _ in range(6):
        await asyncio.sleep(0)


async def test_four_simultaneous_reads_use_one_network_request(backend):
    devices = [client(backend) for _ in range(4)]
    results = await asyncio.gather(*(d.async_get_tv_states() for d in devices))
    assert len(backend.calls) == 1
    assert all(result["volume"] == 10 for result in results)


async def test_pending_ip_read_keys_do_not_expose_the_pairing_token():
    backend = Backend(hold=True)
    device = client(backend, token="synthetic-pairing-credential")
    pending = asyncio.create_task(device.async_get_volume())
    await backend.started.wait()
    assert "synthetic-pairing-credential" not in repr(
        _HOST_LOCKS["192.0.2.10"]._shared_reads
    )
    backend.release.set()
    await pending


async def test_sixty_poll_batches_keep_every_batch_fresh(backend):
    devices = [client(backend) for _ in range(4)]
    for volume in range(60):
        backend.volume = volume
        values = await asyncio.gather(*(d.async_get_volume() for d in devices))
        assert values == [volume] * 4
    assert len(backend.calls) == 60


async def test_sequential_reads_still_make_fresh_requests(backend):
    device = client(backend)
    assert await device.async_get_volume() == 10
    backend.volume = 20
    assert await device.async_get_volume() == 20
    assert len(backend.calls) == 2


async def test_a_read_that_already_started_is_not_shared():
    backend = Backend(hold=True)
    device = client(backend)
    old = asyncio.create_task(device.async_get_volume())
    await backend.started.wait()
    backend.volume = 20
    new = asyncio.create_task(device.async_get_volume())
    await settle()
    backend.release.set()
    assert await old == 10
    assert await new == 20
    assert len(backend.calls) == 2


async def test_shared_dicts_are_independent_for_each_caller(backend):
    first, second = await asyncio.gather(
        client(backend).async_get_tv_states(), client(backend).async_get_tv_states()
    )
    assert len(backend.calls) == 1
    first["nested"]["value"] = "changed"
    assert second["nested"]["value"] == "original"


@pytest.mark.parametrize(
    "difference", ["token", "port", "method", "timeout", "hass", "host"]
)
async def test_different_request_contexts_are_not_combined(backend, difference):
    first = client(backend)
    second_backend = Backend() if difference == "hass" else backend
    second = client(
        second_backend,
        token="other-synthetic" if difference == "token" else "synthetic",
        port=1515 if difference == "port" else 1516,
    )
    if difference == "host":
        second._host = "192.0.2.11"
    await asyncio.gather(
        first._async_request("getTVStates"),
        second._async_request(
            "getVideoStates" if difference == "method" else "getTVStates",
            timeout=2 if difference == "timeout" else 5,
        ),
    )
    assert (
        len(backend.calls) + (len(second_backend.calls) if difference == "hass" else 0)
        == 2
    )


async def test_writes_and_priority_reads_are_never_combined(backend):
    device = client(backend)
    await asyncio.gather(device.async_set_volume(20), device.async_set_volume(20))
    assert len(backend.calls) == 2
    with priority_requests():
        await asyncio.gather(device.async_get_volume(), device.async_get_volume())
    assert len(backend.calls) == 4


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("createAccessToken", {"include_token": False}),
        ("getTVStates", {"include_token": False}),
        ("artModeControl", {}),  # Art-mode verification needs independent samples.
        ("unrecognizedMethod", {}),
    ],
)
async def test_pairing_and_unapproved_getters_are_not_combined(backend, method, kwargs):
    device = client(backend)
    await asyncio.gather(
        device._async_request(method, **kwargs), device._async_request(method, **kwargs)
    )
    assert len(backend.calls) == 2


@pytest.mark.parametrize("change", ["token", "port"])
async def test_a_changed_client_context_is_checked_before_a_queued_read_starts(change):
    backend = Backend(hold=True)
    first = client(backend)
    second = client(backend)
    holding = asyncio.create_task(first._async_request("testHold"))
    await backend.started.wait()
    one = asyncio.create_task(first.async_get_tv_states())
    two = asyncio.create_task(second.async_get_tv_states())
    await settle()
    if change == "token":
        first.set_token("new-synthetic")
    else:
        first._port = 1515
    backend.release.set()
    await asyncio.gather(holding, one, two)
    assert len(backend.calls) == 3


async def test_a_queued_write_separates_reads_from_before_and_after_it():
    backend = Backend(hold=True)
    device = client(backend)
    holding = asyncio.create_task(device._async_request("testHold"))
    await backend.started.wait()
    before = asyncio.create_task(device.async_get_volume())
    await settle()
    write = asyncio.create_task(device.async_set_volume(20))
    await settle()
    after = asyncio.create_task(device.async_get_volume())
    await settle()
    backend.release.set()
    await holding
    assert await before == 10
    assert await write == 20
    assert await after == 20
    assert [method for method, _ in backend.calls] == [
        "testHold",
        "directVolumeControl",
        "directVolumeControl",
        "directVolumeControl",
    ]


async def test_cancelling_one_waiter_keeps_the_other_request_alive():
    backend = Backend(hold=True)
    device = client(backend)
    first = asyncio.create_task(device.async_get_volume())
    second = asyncio.create_task(device.async_get_volume())
    await backend.started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    backend.release.set()
    assert await second == 10
    assert len(backend.calls) == 1


async def test_cancelling_all_queued_waiters_does_not_send_an_unused_read():
    backend = Backend(hold=True)
    device = client(backend)
    holding = asyncio.create_task(device._async_request("testHold"))
    await backend.started.wait()
    reads = [asyncio.create_task(device.async_get_tv_states()) for _ in range(3)]
    await settle()
    for read in reads:
        read.cancel()
    await asyncio.gather(*reads, return_exceptions=True)
    await settle()
    backend.release.set()
    await holding
    assert len(backend.calls) == 1


async def test_a_cancelled_queued_read_cannot_cancel_a_later_caller():
    backend = Backend(hold=True)
    device = client(backend)
    holding = asyncio.create_task(device._async_request("testHold"))
    await backend.started.wait()
    cancelled = asyncio.create_task(device.async_get_volume())
    await settle()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    later = asyncio.create_task(device.async_get_volume())
    await settle()
    backend.release.set()
    await holding
    assert await later == 10
    assert len(backend.calls) == 2


async def test_cancelled_inflight_read_keeps_the_host_gate_until_it_finishes():
    backend = Backend(hold=True)
    device = client(backend)
    read = asyncio.create_task(device.async_get_volume())
    await backend.started.wait()
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    next_read = asyncio.create_task(device.async_get_volume())
    await settle()
    assert len(backend.calls) == 1
    backend.release.set()
    assert await next_read == 10
    assert len(backend.calls) == 2


async def test_failures_reach_all_waiters_and_the_next_read_retries(backend):
    backend.error = SamsungIPControlError("synthetic transport failure")
    results = await asyncio.gather(
        client(backend).async_get_tv_states(),
        client(backend).async_get_tv_states(),
        return_exceptions=True,
    )
    assert all(isinstance(result, SamsungIPControlError) for result in results)
    assert len(backend.calls) == 1
    backend.error = None
    assert (await client(backend).async_get_tv_states())["volume"] == 10
    assert len(backend.calls) == 2
