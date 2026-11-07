"""A half-woken TV is woken again, without a KEY_POWER toggle (#319).

7 November, QE55LS03BGUXXU, Power on method WOL. Art Mode ON at 13:12:36:

    13:12:36.013  KEY_POWER on the remote channel ... 13:12:42 timed out
    13:12:43.037  WOL packet sent (5 time(s))
    13:12:42→13:13 REST answers PowerState=standby (it never did before)
    13:12:44→13:13:03 ready-wait: support check passes, TV reads as off, ×20
    13:13:03      Art Mode deferred; the panel stayed dark
    13:17:36      a manual power-on: WOL, PowerState=on 1.4 s later

The packet woke the network, not the panel, and the power-on's one KEY_POWER
had gone out while the TV could not hear it. Nothing was sent once it could.
The ready-wait now asks the media player for another wake after 3 s and 10 s
of "answers but off"; the media player re-sends its configured wake and never
KEY_POWER, a toggle that would turn off a set that is booting or that woke
into Art Mode while still reporting standby.
"""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from homeassistant.components.media_player import MediaPlayerState  # noqa: E402

from custom_components.samsungtv_smart import media_player as mp  # noqa: E402
from custom_components.samsungtv_smart import switch as sw  # noqa: E402
from custom_components.samsungtv_smart.const import (  # noqa: E402
    CONF_POWER_ON_METHOD,
    DOMAIN,
    SIGNAL_WAKE_NUDGE,
    PowerOnMethod,
)

ENTRY_ID = "entry-319"
SIGNAL = f"{SIGNAL_WAKE_NUDGE}_{ENTRY_ID}"


# ── the ready-wait asks for it ──────────────────────────────────────────────


def _switch(on_after=None):
    """An art switch whose TV passes the support check; on at ``on_after``."""
    entity = object.__new__(sw.FrameArtModeSwitch)
    entity._log = MagicMock()
    entity._device_name = "Frame TV"
    entity._entry = SimpleNamespace(entry_id=ENTRY_ID)
    entity._hass = SimpleNamespace(data={DOMAIN: {ENTRY_ID: {}}})
    entity._art_api = SimpleNamespace(supported=AsyncMock(return_value=True))
    calls = {"n": 0}

    async def is_on():
        calls["n"] += 1
        return on_after is not None and calls["n"] >= on_after

    entity._is_tv_on = is_on
    return entity


async def _wait(entity, max_wait=20):
    sent = MagicMock()
    with (
        patch.object(sw, "async_dispatcher_send", sent),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        ready = await entity._wait_for_tv_ready(max_wait=max_wait)
    return ready, sent


async def test_a_tv_that_answers_but_stays_off_is_woken_twice():
    ready, sent = await _wait(_switch())
    assert ready is False
    # After 3 s and after 10 s of "answers but off", never more.
    assert [c.args[1] for c in sent.call_args_list] == [SIGNAL, SIGNAL]


async def test_a_tv_that_comes_on_quickly_is_not_nudged():
    ready, sent = await _wait(_switch(on_after=2))
    assert ready is True
    sent.assert_not_called()


async def test_one_nudge_then_on():
    ready, sent = await _wait(_switch(on_after=5))
    assert ready is True
    assert sent.call_count == 1


async def test_a_silent_tv_is_not_nudged():
    # Nothing answers: the first wake is still in flight or the TV is off the
    # network. Another magic packet would change nothing the wait can see.
    entity = _switch()
    entity._art_api = SimpleNamespace(supported=AsyncMock(return_value=False))
    ready, sent = await _wait(entity)
    assert ready is False
    sent.assert_not_called()


# ── the media player answers it ─────────────────────────────────────────────


def _player(method, st=None, ip=None):
    device = object.__new__(mp.SamsungTVDevice)
    device._log = MagicMock()
    device._host = "192.0.2.25"
    device._st = st
    device.hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(return_value=True),
    )
    device._get_option = lambda key, default=None: (
        method.value if key == CONF_POWER_ON_METHOD else default
    )
    device._get_ip_control_client = lambda: ip
    device._send_wol_packet = MagicMock(return_value=True)
    device.async_send_command = AsyncMock()
    return device


def _nudge(device, state=MediaPlayerState.OFF):
    with patch.object(mp.SamsungTVDevice, "state", new=property(lambda self: state)):
        asyncio.run(device._async_wake_nudge())


def test_wol_is_resent_and_no_key_power():
    device = _player(PowerOnMethod.WOL)
    _nudge(device)
    device.hass.async_add_executor_job.assert_awaited_once_with(device._send_wol_packet)
    device.async_send_command.assert_not_called()


def test_smartthings_sends_its_on():
    st = SimpleNamespace(async_turn_on=AsyncMock())
    device = _player(PowerOnMethod.SmartThings, st=st)
    _nudge(device)
    st.async_turn_on.assert_awaited_once()
    device.hass.async_add_executor_job.assert_not_called()
    device.async_send_command.assert_not_called()


def test_ip_control_sends_power_on_and_falls_back_to_wol():
    ip = SimpleNamespace(async_power_on=AsyncMock())
    device = _player(PowerOnMethod.IPControl, ip=ip)
    _nudge(device)
    ip.async_power_on.assert_awaited_once()
    device.hass.async_add_executor_job.assert_not_called()

    failing = SimpleNamespace(
        async_power_on=AsyncMock(side_effect=mp.SamsungIPControlError("down"))
    )
    device = _player(PowerOnMethod.IPControl, ip=failing)
    _nudge(device)
    device.hass.async_add_executor_job.assert_awaited_once_with(device._send_wol_packet)
    device.async_send_command.assert_not_called()


def test_a_tv_that_is_already_on_gets_nothing():
    st = SimpleNamespace(async_turn_on=AsyncMock())
    device = _player(PowerOnMethod.SmartThings, st=st)
    _nudge(device, state=MediaPlayerState.ON)
    st.async_turn_on.assert_not_called()
    device.hass.async_add_executor_job.assert_not_called()
    device.async_send_command.assert_not_called()


def test_the_media_player_listens_for_it():
    source = open(mp.__file__, encoding="utf-8").read()
    assert 'f"{SIGNAL_WAKE_NUDGE}_{self._entry_id}"' in source
    assert "self._on_wake_nudge" in source
