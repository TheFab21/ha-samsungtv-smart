"""An accepted IP Control powerOff gets time to reach standby (#461).

A 2023 32" Frame (QN32LS03C), local only, IP Control paired:

    21:28:46.489  IP Control power off was not confirmed; falling back
    21:28:46.490  Sending key KEY_POWER
    21:28:47.298  Art API: go_to_standby
    21:28:47.486  REST PowerState: standby
    21:28:49.491  Sending key KEY_POWER            <- end of the 3 s hold
    21:28:51.075  REST PowerState: on              <- back on

The power state was read back twice, 0.5 s apart; this TV needs ~2.3 s. The
KEY_POWER fallback then landed in the transition and woke the TV, 3 times out
of 4. Now the read-back runs for POWER_OFF_CONFIRM_WINDOW, a go_to_standby
announced since the command counts as confirmation, and a read that fails
mid-transition is not a reason to fall back.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.samsungtv_smart.api import ipcontrol
from custom_components.samsungtv_smart.api.ipcontrol import (
    SamsungIPControlAuthError,
    SamsungIPControlError,
    SamsungIPControlTransportError,
    async_power_off_confirmed,
)


@pytest.fixture
def clock(monkeypatch):
    """A clock that advances 0.5 s per read, like the real poll interval."""
    state = {"now": 100.0}

    def monotonic():
        return state["now"]

    async def sleep(seconds):
        state["now"] += seconds

    monkeypatch.setattr(ipcontrol, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(
        ipcontrol, "asyncio", SimpleNamespace(sleep=sleep), raising=True
    )
    return state


def _client(*reads):
    """powerOff accepted, then the scripted power-state reads (last repeats)."""
    script = list(reads)
    seen_priority = []

    async def get_power_state(**_kwargs):
        seen_priority.append(ipcontrol._PRIORITY_REQUEST.get())
        item = script.pop(0) if len(script) > 1 else script[0]
        if isinstance(item, Exception):
            raise item
        return item

    client = SimpleNamespace(
        async_power_off=AsyncMock(return_value="powerOff"),
        async_get_power_state=AsyncMock(side_effect=get_power_state),
    )
    client.seen_priority = seen_priority
    return client


async def test_a_standby_that_takes_two_seconds_is_still_confirmed(clock):
    # 0, 0.5, 1.0, 1.5 s still powerOn; standby read at 2.0 s.
    client = _client("powerOn", "powerOn", "powerOn", "powerOn", "powerOff")
    assert await async_power_off_confirmed(client) is True
    assert client.async_get_power_state.await_count == 5


async def test_go_to_standby_since_the_command_confirms_it(clock):
    client = _client("powerOn")
    announced = iter([False, False, True])
    assert (
        await async_power_off_confirmed(
            client, standby_announced=lambda: next(announced)
        )
        is True
    )
    assert client.async_get_power_state.await_count == 3


async def test_a_read_that_fails_mid_transition_does_not_end_the_wait(clock):
    client = _client(
        "powerOn",
        SamsungIPControlTransportError("The read operation timed out"),
        SamsungIPControlError("TV returned error -32000"),
        "powerOff",
    )
    assert await async_power_off_confirmed(client) is True


async def test_never_confirmed_gives_up_after_the_window(clock):
    client = _client("powerOn")
    start = clock["now"]
    assert await async_power_off_confirmed(client) is False
    waited = clock["now"] - start
    assert ipcontrol.POWER_OFF_CONFIRM_WINDOW <= waited
    assert waited < ipcontrol.POWER_OFF_CONFIRM_WINDOW + 1


async def test_a_refused_power_off_raises_at_once(clock):
    # Nothing was accepted: the caller can fall back straight away.
    client = _client("powerOn")
    client.async_power_off.side_effect = SamsungIPControlTransportError("refused")
    with pytest.raises(SamsungIPControlTransportError):
        await async_power_off_confirmed(client)
    client.async_get_power_state.assert_not_awaited()


async def test_a_rejected_token_on_the_read_back_is_raised(clock):
    client = _client(SamsungIPControlAuthError("token rejected"))
    with pytest.raises(SamsungIPControlAuthError):
        await async_power_off_confirmed(client)


async def test_read_backs_are_user_initiated_and_short(clock):
    client = _client("powerOn", "powerOff")
    await async_power_off_confirmed(client)
    assert all(client.seen_priority)
    for call in client.async_get_power_state.await_args_list:
        assert call.kwargs["timeout"] == ipcontrol.POWER_OFF_POLL_TIMEOUT
