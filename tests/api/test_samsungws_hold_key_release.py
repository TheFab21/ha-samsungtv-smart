"""A held key is released on the channel that pressed it, or not at all (#461).

With IP Control still refusing connections after a power cut, a 32" Frame
(QN32LS03C) was powered off with the remote's KEY_POWER hold: Press, 3 s,
Release. The Press sent it to standby, which closes the remote channel; the
Release then went through _ws_send, which opened a new connection for it, and
the TV came back on (08:20:55.988 Press, 08:20:58.989 Release).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.samsungtv_smart.api import samsungws
from custom_components.samsungtv_smart.api.samsungws import SamsungTVWS


@pytest.fixture
def tv(monkeypatch):
    ws = object.__new__(SamsungTVWS)
    ws._log = MagicMock()
    ws._ws_remote = object()
    ws.sent = []
    ws.during_hold = None

    def send_key(key, key_press_delay=None, cmd="Click"):
        ws.sent.append(cmd)
        return True

    def sleep(_seconds):
        if ws.during_hold:
            ws.during_hold(ws)

    ws.send_key = send_key
    monkeypatch.setattr(samsungws, "time", SimpleNamespace(sleep=sleep))
    return ws


def test_a_channel_that_closes_during_the_hold_gets_no_release(tv):
    # The TV went to standby on the Press and dropped the remote channel.
    def drop(ws):
        ws._ws_remote = None

    tv.during_hold = drop
    assert tv.hold_key("KEY_POWER", 3) is True
    assert tv.sent == ["Press"]


def test_a_reconnected_channel_gets_no_release_either(tv):
    # A new channel never pressed the key.
    def replace(ws):
        ws._ws_remote = object()

    tv.during_hold = replace
    tv.hold_key("KEY_POWER", 3)
    assert tv.sent == ["Press"]


def test_the_same_channel_is_released_as_before(tv):
    assert tv.hold_key("KEY_POWER", 3) is True
    assert tv.sent == ["Press", "Release"]


def test_without_a_remote_channel_at_the_press_the_release_is_unchanged(tv):
    # The Press itself went over a one-off connection: keep the old behaviour.
    tv._ws_remote = None
    tv.hold_key("KEY_VOLUP", 1)
    assert tv.sent == ["Press", "Release"]


def test_a_press_that_failed_sends_nothing_else(tv):
    def send_key(key, key_press_delay=None, cmd="Click"):
        tv.sent.append(cmd)
        return False

    tv.send_key = send_key
    assert tv.hold_key("KEY_POWER", 3) is False
    assert tv.sent == ["Press"]
