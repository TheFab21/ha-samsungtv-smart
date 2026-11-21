"""IP Control circuit breaker for background reads (#409).

A 2018 UE82NU8005 going into standby kept accepting connections on port 1515
but stopped completing the TLS handshake. The per-host gate serializes calls, so
a speaker read, an Art Mode read and a volume read each spent CMD_TIMEOUT one
after another and the media player update took 11 s, every 30 s for minutes.
A failure that cost time now makes the background reads queued behind it fail
at once; writes, pairing and user-initiated calls still try.
"""

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.samsungtv_smart.api import ipcontrol
from custom_components.samsungtv_smart.api.ipcontrol import (
    SamsungIPControl,
    SamsungIPControlModeLockedError,
    SamsungIPControlTransportError,
    priority_requests,
)

HOST = "192.0.2.20"
TIMED_OUT = SamsungIPControlTransportError("The handshake operation timed out")
REFUSED = SamsungIPControlTransportError("[Errno 111] Connection refused")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


class Backend:
    """Stands in for hass: each executor job takes `cost` seconds of clock."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[str] = []
        self.outcomes: list[tuple[float, Exception | None]] = []
        self.release = asyncio.Event()
        self.release.set()

    def will(self, cost: float, error: Exception | None = None) -> None:
        self.outcomes.append((cost, error))

    async def async_add_executor_job(self, function, method, params, *args):
        self.calls.append(method)
        cost, error = self.outcomes.pop(0) if self.outcomes else (0.1, None)
        await self.release.wait()
        self.clock.now += cost
        if error is not None:
            raise error
        return {"power": "powerOn", "volume": 10}


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ipcontrol, "time", SimpleNamespace(monotonic=clock.monotonic))
    ipcontrol._HOST_LOCKS.clear()
    ipcontrol._UNREACHABLE_UNTIL.clear()
    yield clock
    ipcontrol._HOST_LOCKS.clear()
    ipcontrol._UNREACHABLE_UNTIL.clear()


@pytest.fixture
def backend(clock):
    return Backend(clock)


def tv(backend, port=1515):
    return SamsungIPControl(backend, HOST, token="synthetic", port=port)


async def test_reads_queued_behind_a_timeout_fail_without_their_own_timeout(backend):
    backend.release.clear()
    backend.will(5.0, TIMED_OUT)
    client = tv(backend)
    reads = [
        asyncio.ensure_future(client._async_request(method))
        for method in ("speakerSelectControl", "artModeControl", "directVolumeControl")
    ]
    await asyncio.sleep(0)
    backend.release.set()
    results = await asyncio.gather(*reads, return_exceptions=True)

    assert all(isinstance(r, SamsungIPControlTransportError) for r in results)
    # Only the first one went to the wire: 5 s in all instead of 15 s.
    assert len(backend.calls) == 1
    assert sum("skipped" in str(r) for r in results) == 2


async def test_after_the_backoff_the_next_read_probes_and_success_closes_it(
    backend, clock
):
    client = tv(backend)
    backend.will(5.0, TIMED_OUT)
    with pytest.raises(SamsungIPControlTransportError):
        await client._async_request("powerControl")

    clock.now += ipcontrol._UNREACHABLE_BACKOFF + 0.1
    assert (await client._async_request("powerControl"))["power"] == "powerOn"
    assert await client._async_request("directVolumeControl")
    assert backend.calls == ["powerControl", "powerControl", "directVolumeControl"]
    assert not ipcontrol._UNREACHABLE_UNTIL


async def test_a_probe_that_times_out_again_rearms_the_breaker(backend, clock):
    client = tv(backend)
    backend.will(5.0, TIMED_OUT)
    backend.will(5.0, TIMED_OUT)
    for _ in range(2):
        with pytest.raises(SamsungIPControlTransportError):
            await client._async_request("powerControl")
        clock.now += ipcontrol._UNREACHABLE_BACKOFF + 0.1
    clock.now -= 1.0  # still inside the second window
    with pytest.raises(SamsungIPControlTransportError, match="skipped"):
        await client._async_request("powerControl")
    assert len(backend.calls) == 2


async def test_a_refused_connection_costs_nothing_and_opens_nothing(backend):
    # A TV that is simply off refuses at once: keep probing it every poll, so
    # its wake-up is seen on the next one.
    client = tv(backend)
    backend.will(0.01, REFUSED)
    with pytest.raises(SamsungIPControlTransportError):
        await client._async_request("powerControl")
    await client._async_request("powerControl")
    assert backend.calls == ["powerControl", "powerControl"]


async def test_writes_and_user_initiated_reads_still_go_to_the_wire(backend):
    client = tv(backend)
    backend.will(5.0, TIMED_OUT)
    with pytest.raises(SamsungIPControlTransportError):
        await client._async_request("powerControl")

    await client._async_request("powerControl", {"power": "powerOff"})
    # The write was answered, so the breaker is closed again.
    assert not ipcontrol._UNREACHABLE_UNTIL

    backend.will(5.0, TIMED_OUT)
    with pytest.raises(SamsungIPControlTransportError):
        await client._async_request("powerControl")
    with priority_requests():
        await client._async_request("artModeControl")
    assert backend.calls == [
        "powerControl",
        "powerControl",
        "powerControl",
        "artModeControl",
    ]


async def test_pairing_neither_opens_nor_is_blocked_by_the_breaker(backend):
    client = tv(backend)
    # The on-screen prompt left unanswered times out after a long wait.
    backend.will(30.0, TIMED_OUT)
    with pytest.raises(SamsungIPControlTransportError):
        await client._async_request(
            "createAccessToken", include_token=False, timeout=30
        )
    assert not ipcontrol._UNREACHABLE_UNTIL

    backend.will(5.0, TIMED_OUT)
    with pytest.raises(SamsungIPControlTransportError):
        await client._async_request("powerControl")
    await client._async_request("createAccessToken", include_token=False, timeout=30)
    assert backend.calls[-1] == "createAccessToken"


async def test_an_error_reply_proves_the_tv_answers_and_closes_it(backend, clock):
    client = tv(backend)
    backend.will(5.0, TIMED_OUT)
    with pytest.raises(SamsungIPControlTransportError):
        await client._async_request("powerControl")
    clock.now += ipcontrol._UNREACHABLE_BACKOFF + 0.1
    backend.will(0.1, SamsungIPControlModeLockedError("-32003"))
    with pytest.raises(SamsungIPControlModeLockedError):
        await client._async_request("directVolumeControl")
    await client._async_request("powerControl")
    assert len(backend.calls) == 3


async def test_the_other_port_of_the_same_tv_is_not_affected(backend):
    backend.will(5.0, TIMED_OUT)
    with pytest.raises(SamsungIPControlTransportError):
        await tv(backend, port=1516)._async_request("powerControl")
    await tv(backend, port=1515)._async_request("powerControl")
    assert len(backend.calls) == 2


async def test_time_spent_waiting_for_the_gate_is_not_counted(backend):
    # A fast failure that merely queued behind a slow success is not slow.
    backend.release.clear()
    backend.will(4.0, None)
    backend.will(0.01, REFUSED)
    client = tv(backend)
    first = asyncio.ensure_future(client._async_request("artModeControl"))
    second = asyncio.ensure_future(client._async_request("backlightControl"))
    await asyncio.sleep(0)
    backend.release.set()
    await first
    with pytest.raises(SamsungIPControlTransportError):
        await second
    assert not ipcontrol._UNREACHABLE_UNTIL
