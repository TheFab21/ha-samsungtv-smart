from types import SimpleNamespace

import pytest

from custom_components.samsungtv_smart.const import (
    DATA_IP_CONTROL_STATE_COORDINATOR,
    DOMAIN,
)

from .test_art_mode_getter_read_without_option import (
    ENTRY_ID,
    _art_mode_is_on,
    _device,
    _refresh,
)


def shared(device, mode="Ambient", observed_at=100.0):
    coordinator = SimpleNamespace(
        last_update_success=True,
        data={"tv": {"pictureMode": mode}, "polled_at": observed_at},
    )
    device.hass.data[DOMAIN][ENTRY_ID][DATA_IP_CONTROL_STATE_COORDINATOR] = coordinator
    device.hass.config_entries = SimpleNamespace(
        async_get_entry=lambda _: SimpleNamespace(options={})
    )
    return coordinator


@pytest.mark.parametrize(("mode", "expected"), [("Ambient", True), ("Movie", False)])
async def test_fresh_shared_status_needs_no_separate_tv_reads(mode, expected):
    device, client = _device()
    shared(device, mode)
    await _refresh(device, now=101.0)
    client.async_get_power_state.assert_not_awaited()
    client.async_get_art_mode.assert_not_awaited()
    assert device._ip_art_mode is expected
    assert device._ip_art_mode_at == 100.0


@pytest.mark.parametrize(
    "problem",
    ["stale", "future", "failed", "powered_off", "missing", "unknown", "bool_time"],
)
async def test_unusable_shared_status_keeps_the_existing_direct_fallback(problem):
    device, client = _device()
    coordinator = shared(device)
    if problem == "stale":
        coordinator.data["polled_at"] = 0.0
    if problem == "future":
        coordinator.data["polled_at"] = 120.0
    if problem == "failed":
        coordinator.last_update_success = False
    if problem == "powered_off":
        coordinator.data["powered_off"] = True
    if problem == "missing":
        coordinator.data["tv"] = {}
    if problem == "unknown":
        coordinator.data["tv"]["pictureMode"] = "unknown"
    if problem == "bool_time":
        coordinator.data["polled_at"] = True
    await _refresh(device, now=101.0)
    client.async_get_power_state.assert_awaited_once()
    client.async_get_art_mode.assert_awaited_once_with(power_state="powerOn")


async def test_a_non_frame_ambient_mode_does_not_become_art_mode():
    device, client = _device(frame=False)
    shared(device)
    await _refresh(device, now=101.0)
    client.async_get_power_state.assert_awaited_once()
    client.async_get_art_mode.assert_not_awaited()
    assert device._ip_art_mode is None


async def test_a_snapshot_before_a_broadcast_cannot_answer_its_forced_refresh():
    device, client = _device(broadcast=(True, 101.0), getter=False)
    shared(device, "Movie", observed_at=100.0)
    await _refresh(device, now=102.0)
    client.async_get_art_mode.assert_awaited_once()
    assert _art_mode_is_on(device, now=102.0) is True


async def test_shared_status_cannot_replace_a_newer_confirmed_power_off():
    device, client = _device(power="powerOff")
    await _refresh(device, now=100.0)
    shared(device, observed_at=99.0)
    await _refresh(device, now=101.0)
    assert device._ip_art_mode is False
    assert client.async_get_power_state.await_count == 2
    shared(device, observed_at=102.0)
    await _refresh(device, now=103.0)
    assert device._ip_art_mode is True
    assert client.async_get_power_state.await_count == 2


async def test_reading_before_a_newer_direct_read_is_not_reused():
    device, client = _device()
    device._ip_art_mode_at = 101.0
    shared(device, observed_at=100.0)
    await _refresh(device, now=102.0)
    client.async_get_power_state.assert_awaited_once()


async def test_no_shared_status_keeps_refreshing_at_the_existing_cadence():
    device, client = _device()
    await _refresh(device, now=100.0)
    await _refresh(device, now=105.0)
    assert client.async_get_power_state.await_count == 2
    assert client.async_get_art_mode.await_count == 2


async def test_refresh_without_a_shared_coordinator_keeps_the_direct_path():
    device, client = _device()
    device.hass = None
    await _refresh(device, now=100.0)
    client.async_get_power_state.assert_awaited_once()
    client.async_get_art_mode.assert_awaited_once()
