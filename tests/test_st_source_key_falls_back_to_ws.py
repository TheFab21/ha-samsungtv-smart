"""A source key must not need the cloud when the LAN can do the same thing.

A source list auto-discovered from SmartThings holds only ST_ keys:

    loaded sources list from SmartThings: {'TV': 'ST_TV', 'CINEMA 60': 'ST_HDMI3'}

So every source switch went to the cloud, and the cloud being unreachable took
the feature down with it. Measured on a QE55LS03D, 27 October:

    07:24:55.425  INFO    Art Mode turned off: switching to 'HDMI3'
    07:24:55.425  WARNING SmartThings not available. Command not sent: ST_HDMI3
    07:24:55.752  {'event': 'go_to_standby'}

The panel went to standby instead of CINEMA 60. That is the wrong order of
preference now that SmartThings API access is a paid plan, and on a Frame the
WebSocket key is the ONLY local way to pick an input: IP Control's
inputSourceControl is not implemented there (IP_Control_Protocol_Reference.md
-- "To set input on a Frame: WebSocket KEY_HDMIx or SmartThings").

Nothing is promised about the key landing, which is the point of the read-back
here: KEY_HDMI<n> is NOT in docs/Key_codes.md -- only the generic KEY_HDMI is
-- so a model that ignores it has to be told apart from one that obeys.
"""

import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

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

from custom_components.samsungtv_smart import media_player as mp  # noqa: E402
from custom_components.samsungtv_smart.api.ipcontrol import (  # noqa: E402
    SamsungIPControlError,
)

Device = mp.SamsungTVDevice


# --- the mapping, which is the whole safety of the fallback ----------------


def test_hdmi_keys_map_one_for_one():
    for n in (1, 2, 3, 4):
        assert Device._ws_key_for_st_key(f"ST_HDMI{n}") == f"KEY_HDMI{n}"


def test_the_tuner_and_the_step_keys_map():
    assert Device._ws_key_for_st_key("ST_TV") == "KEY_TV"
    assert Device._ws_key_for_st_key("ST_CHUP") == "KEY_CHUP"
    assert Device._ws_key_for_st_key("ST_CHDOWN") == "KEY_CHDOWN"
    assert Device._ws_key_for_st_key("ST_VOLUP") == "KEY_VOLUP"
    assert Device._ws_key_for_st_key("ST_VOLDOWN") == "KEY_VOLDOWN"


def test_a_state_command_is_not_mapped_to_a_toggle():
    # ST_MUTE / ST_UNMUTE ask for a state; KEY_MUTE toggles. Substituting one
    # for the other would turn "mute on" into "mute off" half the time.
    assert Device._ws_key_for_st_key("ST_MUTE") is None
    assert Device._ws_key_for_st_key("ST_UNMUTE") is None


def test_values_no_single_key_expresses_are_not_mapped():
    for key in ("ST_VOL25", "ST_CH101", "ST_VD:3201907018807", "ST_WHATEVER"):
        assert Device._ws_key_for_st_key(key) is None, key


def test_only_input_keys_claim_a_readback_value():
    assert Device._input_for_ws_key("KEY_HDMI3") == "HDMI3"
    assert Device._input_for_ws_key("KEY_TV") == "TV"
    # Volume and channel do not change the input, so there is nothing to read.
    assert Device._input_for_ws_key("KEY_VOLUP") is None
    assert Device._input_for_ws_key("KEY_CHUP") is None


# --- the fallback itself ---------------------------------------------------


def _device(*, st=None, input_source="TV", ip_control=True, send_ok=True, raises=False):
    device = object.__new__(Device)
    device._log = MagicMock()
    device._st = st
    device.sent = []

    async def _send(key):
        device.sent.append(key)
        return send_ok

    device.async_send_command = _send

    async def _states():
        if raises:
            raise SamsungIPControlError("transport failure")
        return {"inputSource": input_source}

    client = SimpleNamespace(async_get_tv_states=_states)
    device._get_ip_control_client = lambda: client if ip_control else None
    return device


def _run(device, key):
    real_sleep = asyncio.sleep

    async def _instant(_seconds):
        await real_sleep(0)

    asyncio.sleep = _instant
    try:
        return asyncio.run(device._smartthings_keys(key))
    finally:
        asyncio.sleep = real_sleep


def test_the_reported_case_now_switches_the_input():
    # ST unavailable, ST_HDMI3 asked for, TV confirms HDMI3.
    device = _device(st=None, input_source="HDMI3")

    assert _run(device, "ST_HDMI3") is True
    assert device.sent == ["KEY_HDMI3"]


def test_a_model_that_ignores_the_key_is_reported_not_silently_accepted():
    # The key goes out, the TV stays on TV. Failing silently here would be the
    # same class of bug as the one this replaces.
    device = _device(st=None, input_source="TV")

    assert _run(device, "ST_HDMI3") is False
    assert device.sent == ["KEY_HDMI3"]
    assert any(
        "still reports input" in str(c) for c in device._log.warning.call_args_list
    )


def test_without_ip_control_the_send_is_reported_since_it_cannot_be_checked():
    device = _device(st=None, ip_control=False)

    assert _run(device, "ST_HDMI3") is True
    assert device.sent == ["KEY_HDMI3"]


def test_an_unreadable_input_does_not_turn_a_sent_key_into_a_failure():
    device = _device(st=None, raises=True)

    assert _run(device, "ST_HDMI3") is True


def test_a_key_that_cannot_be_sent_is_a_failure():
    device = _device(st=None, send_ok=False)

    assert _run(device, "ST_HDMI3") is False


def test_an_unmappable_key_still_refuses_and_says_why():
    device = _device(st=None)

    assert _run(device, "ST_MUTE") is False
    assert device.sent == []
    assert any(
        "no WebSocket key does the same thing" in str(c)
        for c in device._log.warning.call_args_list
    )


def test_smartthings_offline_takes_the_same_path_as_not_configured():
    st = SimpleNamespace(state="off")  # anything but STATE_ON
    device = _device(st=st, input_source="HDMI3")

    assert _run(device, "ST_HDMI3") is True
    assert device.sent == ["KEY_HDMI3"]


def test_a_working_cloud_is_left_alone():
    # The fallback must not steal the happy path: with SmartThings up, the
    # command goes to SmartThings and no key is sent.
    calls = []

    async def _select(value):
        calls.append(value)

    st = SimpleNamespace(
        state=mp.STStatus.STATE_ON,
        async_select_source=_select,
    )
    device = _device(st=st)

    assert _run(device, "ST_HDMI3") is True
    assert calls == ["HDMI3"]
    assert device.sent == [], "no WebSocket key is owed when the cloud worked"
