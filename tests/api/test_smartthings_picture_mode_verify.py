"""setPictureMode read-back must wait for the cloud before re-sending.

Measured on a Frame 2024 (QE55LS03D): the cloud still reported the previous
mode 6.6 s after an accepted send that the panel had applied. The single read
at 5 s took that for a refusal and sent the same mode again through the next
variant, which the TV answered on screen with "function not available" — and
the variant that happened to be sent second was memorized as the working one,
flipping on every change.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

try:
    import homeassistant.util  # noqa: F401
except ImportError:  # only Throttle is used by api/smartthings.py
    _ha = ModuleType("homeassistant")
    _ha_util = ModuleType("homeassistant.util")
    _ha_util.Throttle = lambda *a, **k: (lambda func: func)
    _ha.util = _ha_util
    sys.modules.setdefault("homeassistant", _ha)
    sys.modules.setdefault("homeassistant.util", _ha_util)

import smartthings as smartthings_module  # noqa: E402

MAP = {"Dynamique": "modeDynamic", "FILMMAKER MODE": "modeFilmmakerMode"}


def _client(reads):
    """A SmartThingsTV whose cloud read-back returns ``reads`` in turn."""
    with patch.object(smartthings_module, "SmartThings"):
        client = smartthings_module.SmartThingsTV(
            api_key="key", device_id="device", session=MagicMock()
        )
    client._state = smartthings_module.STStatus.STATE_ON
    client._picture_mode_map = dict(MAP)
    client._picture_mode_capability = "samsungvd.pictureMode"
    client._send_rest_command = AsyncMock()
    client._async_refresh_device_status = AsyncMock()
    queue = list(reads)

    async def read():
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        return value(client) if callable(value) else value

    client._async_read_picture_mode = read
    return client


def _sent(client):
    return [
        (call.kwargs["capability"], call.kwargs["arguments"][0])
        for call in client._send_rest_command.await_args_list
    ]


@pytest.fixture(autouse=True)
def _fast_verify():
    with (
        patch.object(smartthings_module, "PICTURE_MODE_VERIFY_INTERVAL", 0.001),
        patch.object(smartthings_module, "PICTURE_MODE_VERIFY_TIMEOUT", 0.0055),
    ):
        yield


async def test_a_lagging_cloud_does_not_cause_a_second_send():
    client = _client(["FILMMAKER MODE", "FILMMAKER MODE", "Dynamique"])
    assert await client.async_set_picture_mode("Dynamique") is True
    assert _sent(client) == [("samsungvd.pictureMode", "Dynamique")]
    assert client._verified_picture_mode_capability == "samsungvd.pictureMode|name"


async def test_a_mode_never_reported_still_falls_back_to_the_next_variant():
    client = _client(["FILMMAKER MODE"])
    assert await client.async_set_picture_mode("Dynamique") is False
    assert _sent(client)[:2] == [
        ("samsungvd.pictureMode", "Dynamique"),
        ("samsungvd.pictureMode", "modeDynamic"),
    ]


async def test_a_second_variant_that_verifies_is_not_memorized():
    # Five stale reads exhaust the first window; the match then lands in the
    # second one. The first send may be the one that applied, late.
    client = _client(["FILMMAKER MODE"] * 6 + ["Dynamique"])
    client._verified_picture_mode_capability = None
    assert await client.async_set_picture_mode("Dynamique") is True
    assert len(_sent(client)) == 2
    assert client._verified_picture_mode_capability is None


async def test_unreadable_status_is_unknown_not_a_refusal():
    client = _client([None])
    assert await client.async_set_picture_mode("Dynamique") is None
    assert len(_sent(client)) == 1


async def test_a_newer_request_stops_the_older_one():
    def newer_request_arrives(client):
        client._picture_mode_seq += 1
        return "FILMMAKER MODE"

    client = _client([newer_request_arrives, "FILMMAKER MODE"])
    assert await client.async_set_picture_mode("Dynamique") is None
    assert _sent(client) == [("samsungvd.pictureMode", "Dynamique")]
