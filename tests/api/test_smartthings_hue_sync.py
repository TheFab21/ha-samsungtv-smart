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
        raise_for_status=True,
    )
