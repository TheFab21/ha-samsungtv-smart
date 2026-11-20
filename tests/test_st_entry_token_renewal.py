"""A TV that borrows the SmartThings integration's token must renew it.

With the auth method "SmartThings integration" the TV reads the OAuth token
of the SmartThings integration's config entry. That integration renews its
token only when it makes a request of its own; when it makes none for a day,
the token expires and every SmartThings call of the TV fails with
"Authentication failed with SmartThings" until something reloads the
integration. Reported on a Frame: the errors started 24 hours to the minute
after the token it was reading had been issued, and stopped as soon as the
SmartThings integration was reloaded.

The TV now renews the borrowed token through the integration's own OAuth
session when it has expired, and writes the new one to that entry.
"""

import asyncio
import sys
import time
from types import ModuleType
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components import samsungtv_smart  # noqa: E402
from custom_components.samsungtv_smart import (  # noqa: E402
    async_get_samsungtv_api_key,
    async_get_smartthings_api_key,
)
from custom_components.samsungtv_smart.const import (  # noqa: E402
    AUTH_METHOD_ST_ENTRY,
    CONF_AUTH_METHOD,
    CONF_ST_ENTRY_UNIQUE_ID,
    DOMAIN,
)

ST_UNIQUE_ID = "st-location"
IMPLEMENTATION = (
    "custom_components.samsungtv_smart.config_entry_oauth2_flow."
    "async_get_config_entry_implementation"
)


class _Implementation:
    """OAuth implementation that counts refreshes."""

    def __init__(self, new_token=None, error=None):
        self.calls = []
        self._new_token = new_token
        self._error = error

    async def async_refresh_token(self, token):
        self.calls.append(token)
        if self._error:
            raise self._error
        return self._new_token


def _st_entry(hass, expires_at):
    entry = MockConfigEntry(
        domain="smartthings",
        unique_id=ST_UNIQUE_ID,
        data={
            "auth_implementation": "smartthings",
            "token": {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "expires_at": expires_at,
            },
        },
    )
    entry.add_to_hass(hass)
    samsungtv_smart._st_entry_refresh_retry_at.clear()
    samsungtv_smart._st_entry_refresh_locks.clear()
    return entry


def _new_token():
    return {
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": time.time() + 86400,
    }


async def test_an_expired_borrowed_token_is_renewed(hass):
    entry = _st_entry(hass, expires_at=time.time() - 60)
    implementation = _Implementation(new_token=_new_token())

    with patch(IMPLEMENTATION, return_value=implementation):
        key = await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)

    assert key == "new-access"
    assert len(implementation.calls) == 1
    # Written to the SmartThings entry, so the integration uses it too.
    assert entry.data["token"]["access_token"] == "new-access"
    assert entry.data["token"]["refresh_token"] == "new-refresh"


async def test_a_token_about_to_expire_is_renewed(hass):
    _st_entry(hass, expires_at=time.time() + 10)
    implementation = _Implementation(new_token=_new_token())

    with patch(IMPLEMENTATION, return_value=implementation):
        key = await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)

    assert key == "new-access"
    assert len(implementation.calls) == 1


async def test_concurrent_callers_renew_only_once(hass):
    """The media player, sensors and switches renew one at a time.

    OAuth2Session holds its lock per instance, so callers that each create one
    would all renew with the same refresh token. The ones that wait must find
    the renewed token and not submit the old refresh token again.
    """
    entry = _st_entry(hass, expires_at=time.time() - 60)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    class Implementation:
        async def async_refresh_token(self, token):
            calls.append(token["refresh_token"])
            started.set()
            await release.wait()
            return _new_token()

    with patch(IMPLEMENTATION, return_value=Implementation()):
        first = asyncio.create_task(async_get_smartthings_api_key(hass, ST_UNIQUE_ID))
        await started.wait()
        others = [
            asyncio.create_task(async_get_smartthings_api_key(hass, ST_UNIQUE_ID))
            for _ in range(3)
        ]
        await asyncio.sleep(0)
        release.set()
        keys = await asyncio.gather(first, *others)

    assert calls == ["old-refresh"]
    assert keys == ["new-access"] * 4
    assert entry.data["token"]["access_token"] == "new-access"


async def test_a_valid_token_is_read_without_a_refresh(hass):
    _st_entry(hass, expires_at=time.time() + 3600)
    implementation = _Implementation(new_token=_new_token())

    with patch(IMPLEMENTATION, return_value=implementation):
        key = await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)

    assert key == "old-access"
    assert implementation.calls == []


async def test_a_failed_renewal_keeps_the_token_and_waits_before_retrying(hass, caplog):
    entry = _st_entry(hass, expires_at=time.time() - 60)
    implementation = _Implementation(error=RuntimeError("invalid_grant"))

    with patch(IMPLEMENTATION, return_value=implementation):
        first = await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)
        second = await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)

    assert first == second == "old-access"
    # One attempt, not one per poll.
    assert len(implementation.calls) == 1
    assert entry.data["token"]["access_token"] == "old-access"
    assert "could not renew" in caplog.text


async def test_a_failed_renewal_is_retried_after_the_delay(hass):
    _st_entry(hass, expires_at=time.time() - 60)
    failing = _Implementation(error=RuntimeError("timeout"))
    working = _Implementation(new_token=_new_token())

    with patch(IMPLEMENTATION, return_value=failing):
        await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)
    later = time.time() + samsungtv_smart.ST_ENTRY_REFRESH_RETRY_DELAY + 1
    with (
        patch(IMPLEMENTATION, return_value=working),
        patch("custom_components.samsungtv_smart.time.time", return_value=later),
    ):
        key = await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)

    assert key == "new-access"
    assert len(working.calls) == 1


async def test_a_legacy_pat_is_returned_as_is(hass):
    entry = MockConfigEntry(
        domain="smartthings", unique_id=ST_UNIQUE_ID, data={"token": "pat-token"}
    )
    entry.add_to_hass(hass)

    with patch(IMPLEMENTATION) as implementation:
        key = await async_get_smartthings_api_key(hass, ST_UNIQUE_ID)

    assert key == "pat-token"
    implementation.assert_not_called()


async def test_the_tv_entry_gets_the_renewed_token(hass):
    _st_entry(hass, expires_at=time.time() - 60)
    tv_entry = MockConfigEntry(
        domain=DOMAIN,
        title="The Frame",
        data={
            CONF_AUTH_METHOD: AUTH_METHOD_ST_ENTRY,
            CONF_ST_ENTRY_UNIQUE_ID: ST_UNIQUE_ID,
            "api_key": "old-access",
        },
    )
    tv_entry.add_to_hass(hass)

    with patch(IMPLEMENTATION, return_value=_Implementation(new_token=_new_token())):
        key = await async_get_samsungtv_api_key(hass, tv_entry)

    assert key == "new-access"


def test_the_media_player_renews_before_the_smartthings_poll():
    """The poll path asks for a renewed token on ST_ENTRY entries."""
    from pathlib import Path

    source = (
        Path(__file__).parents[1]
        / "custom_components"
        / "samsungtv_smart"
        / "media_player.py"
    ).read_text()
    update = source[source.index("    async def _async_update(self):") :]
    renew = update.index("await self._async_refresh_st_entry_token()")
    poll = update.index("await self._async_st_update()")
    assert renew < poll
