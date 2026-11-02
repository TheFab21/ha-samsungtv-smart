import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.samsungtv_smart.api import smartthings as api


@pytest.fixture
def clock(monkeypatch):
    current = [1000.0]
    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: current[0]))
    return current


def client():
    instance = object.__new__(api.SmartThingsTV)
    instance._api_key = "synthetic"
    instance._api_key_callback = None
    instance._device_id = "synthetic-device"
    instance._log = MagicMock()
    instance._send_rest_command = AsyncMock(return_value=False)
    return instance


async def test_failure_backoff_is_bounded_and_success_restores_normal_cadence(clock):
    instance = client()
    for delay in (120, 240, 480, 900, 900):
        now = clock[0]
        await instance._periodic_refresh()
        assert instance._next_periodic_refresh == now + delay
        count = instance._send_rest_command.await_count
        clock[0] += delay - 1
        await instance._periodic_refresh()
        assert instance._send_rest_command.await_count == count
        clock[0] += 1
    instance._send_rest_command.return_value = True
    await instance._periodic_refresh()
    assert instance._refresh_failures == 0
    assert instance._next_periodic_refresh == clock[0] + 60


async def test_one_hour_of_failed_refreshes_does_less_work(clock):
    instance = client()
    for seconds in range(0, 3600, 30):
        clock[0] = 1000 + seconds
        await instance._periodic_refresh()
    assert instance._send_rest_command.await_count == 7


async def test_status_reads_continue_while_refresh_is_backed_off(clock):
    instance = client()
    instance._state = api.STStatus.STATE_ON
    instance._st = SimpleNamespace(get_device_status=AsyncMock(return_value={}))
    instance._next_periodic_refresh = clock[0] + 900
    await api.SmartThingsTV.async_device_update.__wrapped__(
        instance, allow_refresh=True
    )
    instance._st.get_device_status.assert_awaited_once_with("synthetic-device")
    instance._send_rest_command.assert_not_awaited()


async def test_explicit_command_verification_refresh_ignores_background_deadline(clock):
    instance = client()
    instance._next_periodic_refresh = clock[0] + 900
    instance._send_rest_command.return_value = True
    assert await instance._async_refresh_device_status() is True
    instance._send_rest_command.assert_awaited_once_with(
        capability="refresh", command="refresh"
    )
    assert instance._next_periodic_refresh == clock[0] + 60


async def test_network_failure_also_backs_off(clock):
    instance = client()
    instance._send_rest_command.side_effect = TimeoutError()
    await instance._periodic_refresh()
    assert instance._next_periodic_refresh == clock[0] + 120


async def test_credential_rotation_allows_an_immediate_recovery_probe(clock):
    instance = client()
    instance._next_periodic_refresh = clock[0] + 900
    instance._refresh_failures = 4
    instance._st = MagicMock()
    instance._api_key_callback = lambda: "rotated-synthetic"
    instance._get_api_key()
    assert instance._next_periodic_refresh == 0
    assert instance._refresh_failures == 0
    instance._st.authenticate.assert_called_once_with("rotated-synthetic")


async def test_concurrent_background_callers_share_one_attempt(clock):
    instance = client()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def send(**_kwargs):
        started.set()
        await finish.wait()
        return True

    instance._send_rest_command.side_effect = send
    pending = asyncio.create_task(instance._periodic_refresh())
    await started.wait()
    await instance._periodic_refresh()
    finish.set()
    await pending
    assert instance._send_rest_command.await_count == 1


async def test_cancelled_refresh_releases_the_background_guard(clock):
    instance = client()
    started = asyncio.Event()

    async def send(**_kwargs):
        started.set()
        await asyncio.Event().wait()

    instance._send_rest_command.side_effect = send
    pending = asyncio.create_task(instance._periodic_refresh())
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert instance._periodic_refresh_in_progress is False


async def test_late_failed_refresh_does_not_override_newer_explicit_success(clock):
    instance = client()
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0

    async def send(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await finish.wait()
            return False
        return True

    instance._send_rest_command.side_effect = send
    pending = asyncio.create_task(instance._periodic_refresh())
    await started.wait()
    await instance._async_refresh_device_status()
    finish.set()
    await pending
    assert instance._refresh_failures == 0
    assert instance._next_periodic_refresh == clock[0] + 60


@pytest.mark.parametrize(
    ("status", "expected"), [("FAILED", False), ("COMPLETED", True), ("ACCEPTED", True)]
)
async def test_rest_result_distinguishes_device_failure_from_http_acceptance(
    status, expected
):
    instance = client()
    response = MagicMock()
    response.status = 200
    response.json = AsyncMock(return_value={"results": [{"status": status}]})
    request = MagicMock()
    request.__aenter__ = AsyncMock(return_value=response)
    request.__aexit__ = AsyncMock(return_value=False)
    instance._session = SimpleNamespace(post=MagicMock(return_value=request))
    assert (
        await api.SmartThingsTV._send_rest_command(instance, "refresh", "refresh")
        is expected
    )


async def test_missing_command_result_does_not_reset_the_failure_backoff(clock):
    instance = client()
    instance._refresh_failures = 2
    instance._send_rest_command.return_value = None
    assert await instance._async_refresh_device_status() is False
    assert instance._refresh_failures == 3
    assert instance._next_periodic_refresh == clock[0] + 480
