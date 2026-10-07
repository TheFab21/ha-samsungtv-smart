"""Application credentials platform for Samsung TV Smart."""

from json import JSONDecodeError
import logging
import time
from typing import cast

from aiohttp import BasicAuth, ClientError

from homeassistant.components.application_credentials import (
    AuthImplementation,
    AuthorizationServer,
    ClientCredential,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2Implementation

from .const import DOMAIN

# The "My Home Assistant" OAuth redirect, used when the live redirect URI
# cannot be read (no "my" integration and no request in context).
_MY_REDIRECT_URL = "https://my.home-assistant.io/redirect/oauth"

# The two static links in the credentials-dialog setup instructions.
DESCRIPTION_PLACEHOLDERS = {
    "smartthings_portal": "https://developer.smartthings.com/",
    "oauth_docs": "https://developer.smartthings.com/docs/connected-services/oauth-integrations",
}

_LOGGER = logging.getLogger(__name__)


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return the placeholders for the "Add application credential" dialog.

    Home Assistant interpolates the credentials-dialog description with these.
    Without them the frontend raised "formatjs Error: MISSING_VALUE" and the
    two links resolved to the dialog's own page (#302). ``callback_url`` is the
    redirect URI this OAuth flow actually uses, so the value the user registers
    in the SmartThings portal matches the one the handshake sends.
    """
    try:
        callback_url = config_entry_oauth2_flow.async_get_redirect_uri(hass)
    except RuntimeError:
        # No "my" integration and no request context (e.g. outside the dialog).
        callback_url = _MY_REDIRECT_URL
    return {**DESCRIPTION_PLACEHOLDERS, "callback_url": callback_url}


# SmartThings OAuth endpoints
AUTHORIZE_URL = "https://api.smartthings.com/oauth/authorize"
TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> AbstractOAuth2Implementation:
    """Return auth implementation."""
    return SmartThingsOAuth2Implementation(
        hass,
        DOMAIN,
        credential,
        authorization_server=AuthorizationServer(
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
        ),
    )


class SmartThingsOAuth2Implementation(AuthImplementation):
    """OAuth2 implementation for SmartThings.

    SmartThings requires HTTP Basic Auth for the token endpoint,
    not the standard POST body credentials.
    """

    async def _token_request(self, data: dict) -> dict:
        """Make a token request with Basic Auth."""
        session = async_get_clientsession(self.hass)

        resp = await session.post(
            self.token_url,
            data=data,
            auth=BasicAuth(self.client_id, self.client_secret),
        )
        if resp.status >= 400:
            try:
                error_response = await resp.json()
            except (ClientError, JSONDecodeError):
                error_response = {}
            error_code = error_response.get("error", "unknown")
            error_description = error_response.get("error_description", "unknown error")
            _LOGGER.error(
                "Token request for %s failed (%s): %s",
                self.domain,
                error_code,
                error_description,
            )
        resp.raise_for_status()

        token = cast(dict, await resp.json())

        # Always recalculate expires_at from expires_in to ensure correctness
        # SmartThings may return an incorrect expires_at or none at all
        if "expires_in" in token:
            token["expires_at"] = time.time() + token["expires_in"]
            _LOGGER.info(
                "Token received: expires_in=%s sec (%.1f hours), expires_at=%s",
                token["expires_in"],
                token["expires_in"] / 3600,
                token["expires_at"],
            )

        return token
