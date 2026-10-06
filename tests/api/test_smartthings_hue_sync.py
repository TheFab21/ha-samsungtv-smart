"""Tests for Philips Hue Sync SmartThings commands."""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        """Return arbitrary SmartThings enum-like attributes."""

        def __getattr__(self, name):
            return name

    # This stub is collected first (tests/api sorts before tests/test_*), so it
    # must carry what sensor.py imports too, or every later module importing
    # sensor.py fails collection with "cannot import name 'Attribute'".
    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from aiohttp import ClientResponseError  # noqa: E402
import smartthings as smartthings_module  # noqa: E402


@pytest.mark.parametrize(
    ("enabled", "mode"),
    [
        (True, "TurnOn"),
        (False, "TurnOff"),
    ],
)
@pytest.mark.asyncio
async def test_async_set_hue_sync_sends_expected_mode(enabled, mode):
    """Hue Sync uses the Samsung light-control capability in the background."""
    response = AsyncMock()
    response.status = 200
    response.json.return_value = {"results": [{"status": "COMPLETED"}]}
    response_context = AsyncMock()
    response_context.__aenter__.return_value = response
    session = MagicMock()
    session.post.return_value = response_context

    with patch.object(smartthings_module, "SmartThings") as smartthings_client:
        client = smartthings_module.SmartThingsTV(
            api_key="test-api-key",
            device_id="test-device-id",
            session=session,
        )
        smartthings_client.return_value.authenticate.assert_called_once_with(
            "test-api-key"
        )

    await client.async_set_hue_sync(enabled)

    session.post.assert_called_once_with(
        "https://api.smartthings.com/v1/devices/test-device-id/commands",
        headers={
            "Authorization": "Bearer test-api-key",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "commands": [
                {
                    "component": "main",
                    "capability": "samsungvd.lightControl",
                    "command": "setLightControlMode",
                    "arguments": [mode],
                }
            ]
        },
    )


def _client(*, post=None, get=None):
    """A SmartThingsTV whose aiohttp session returns the given responses."""
    session = MagicMock()
    for method, response in (("post", post), ("get", get)):
        if response is not None:
            context = AsyncMock()
            context.__aenter__.return_value = response
            getattr(session, method).return_value = context
    with patch.object(smartthings_module, "SmartThings"):
        return smartthings_module.SmartThingsTV(
            api_key="test-api-key", device_id="test-device-id", session=session
        )


def _response(status, body, reason="OK"):
    response = AsyncMock()
    response.status = status
    response.reason = reason
    response.request_info = MagicMock()
    response.history = ()
    response.headers = {}
    if isinstance(body, Exception):
        response.json.side_effect = body
    else:
        response.json.return_value = body
    return response


# ── A refused command keeps SmartThings' reason (#298) ───────────────────────


@pytest.mark.asyncio
async def test_a_409_keeps_the_smartthings_error_code_and_message():
    """#298 only ever showed "409, message='Conflict'": the body was dropped."""
    body = {
        "requestId": "4d1a2b3c",
        "error": {
            "code": "ConflictError",
            "message": "invalid device state",
            "details": [],
        },
    }
    client = _client(post=_response(409, body, reason="Conflict"))

    with pytest.raises(ClientResponseError) as caught:
        await client.async_set_hue_sync(False)

    assert caught.value.status == 409
    assert "ConflictError: invalid device state" in caught.value.message
    assert "requestId 4d1a2b3c" in caught.value.message
    assert "409" in str(caught.value)  # what async_turn_on's 409 check looks for


@pytest.mark.asyncio
async def test_an_unreadable_error_body_still_raises_with_the_status():
    client = _client(post=_response(409, ValueError("not json"), reason="Conflict"))

    with pytest.raises(ClientResponseError) as caught:
        await client.async_set_hue_sync(True)

    assert caught.value.status == 409
    assert caught.value.message == "Conflict"


@pytest.mark.asyncio
async def test_a_422_still_maps_to_capability_unsupported():
    client = _client(post=_response(422, {"error": {"code": "x"}}, reason="Bad"))

    with pytest.raises(smartthings_module.SmartThingsCapabilityUnsupported):
        await client.async_set_hue_sync(True)


# ── Session detection reads placeholders as empty (#298) ─────────────────────

# samsungvd.lightControl of a real idle 55" Frame 2024 (QE55LS03DAUXXN,
# pysmartthings tests/fixtures/device_status/vd_frame_2024.json), timestamps
# dropped. The reporter of #298 has a TQ55LS03DAUXXC, the same LS03D 55".
FRAME_2024_IDLE = {
    "supportedModeMap": {"value": [{"id": "", "name": ""}]},
    "requestId": {"value": ""},
    "selectedMode": {"value": ""},
    "streamControl": {"value": False},
    "selectedAppId": {"value": ""},
    "errorCode": {"value": ""},
    "supportedModes": {"value": [""]},
}

# megaholti's dump with Hue Sync running on a 2025 Frame (#266).
RUNNING = {
    "supportedModes": {"value": ["TurnOn", "TurnOff", "Video", "Games", "Music"]},
    "selectedMode": {"value": "Video"},
    "streamControl": {"value": True},
    "selectedAppId": {"value": "com.lighting.HueSyncService"},
}


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (FRAME_2024_IDLE, False),
        ({}, False),
        ({"supportedModes": {"value": []}}, False),
        ({"supportedModes": {"value": None}}, False),
        (RUNNING, True),
        ({"streamControl": {"value": True}}, True),
        ({"selectedAppId": {"value": "com.lighting.HueSyncService"}}, True),
        ({"supportedModes": {"value": ["", "TurnOn"]}}, True),
    ],
)
@pytest.mark.asyncio
async def test_session_detection(status, expected):
    client = _client(get=_response(200, status))
    assert await client.async_hue_sync_session_active() is expected


@pytest.mark.asyncio
async def test_an_unreadable_status_is_unknown():
    client = _client(get=_response(500, {}, reason="Server Error"))
    assert await client.async_hue_sync_session_active() is None
