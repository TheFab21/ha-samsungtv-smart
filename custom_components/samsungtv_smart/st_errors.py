"""Classify SmartThings API failures, and back off from the cloud per TV.

pysmartthings 4.1.0, which Home Assistant 2026.12 pins for its own SmartThings
integration (and so for this one, which uses whatever version Home Assistant
installs), raises a distinct exception for each refusal the API now returns
once its paid plans are in force. Before it, only 401, 403 and 409/422 raised;
402, 418, 429 and 5xx came back as a body the callers then failed to parse.

    401  SmartThingsAuthenticationFailedError
    402  SmartThingsQuotaExceededError   "Quota exceeded"
    403  SmartThingsForbiddenError       (Home Assistant: entitlement_required)
    418  SmartThingsBlockedError         "flagged this client as problematic"
    429  SmartThingsRateLimitError
    5xx  SmartThingsServerError

Home Assistant reads 403 as "SmartThings API plan required" and 402 as "quota
exceeded: an active plan with remaining quota is needed". The raw aiohttp
calls this integration makes carry the same codes on ClientResponseError.

The classes are matched by name, not imported: an older pysmartthings does not
define the new ones, and this integration does not pin the library.
"""

from __future__ import annotations

from enum import StrEnum
import time

from homeassistant.core import HomeAssistant

from .const import DOMAIN

# Samsung's page for choosing or changing a SmartThings API plan.
MANAGE_PLANS_URL = (
    "https://developer.smartthings.com/console/service-integrations/manage-plans"
)

# How long the cloud is left alone after an account-level refusal (quota or
# blocked client): it will not change on the next poll, and every further call
# only counts against a quota that is already gone.
ST_ACCOUNT_BACKOFF = 300.0
# After a rate-limit answer.
ST_RATE_LIMIT_BACKOFF = 60.0


class StErrorKind(StrEnum):
    """What a SmartThings failure means for the caller."""

    REFUSED = "refused"  # 401 / 403: token, plan, or a device id that is gone
    QUOTA = "quota"  # 402: plan quota used up, or no plan with quota
    BLOCKED = "blocked"  # 418: SmartThings flagged this client
    RATE_LIMITED = "rate_limited"  # 429
    SERVER = "server"  # 5xx: SmartThings' own trouble, transient
    OTHER = "other"


_BY_CLASS: dict[str, StErrorKind] = {
    "SmartThingsAuthenticationFailedError": StErrorKind.REFUSED,
    "SmartThingsForbiddenError": StErrorKind.REFUSED,
    "SmartThingsQuotaExceededError": StErrorKind.QUOTA,
    "SmartThingsBlockedError": StErrorKind.BLOCKED,
    "SmartThingsRateLimitError": StErrorKind.RATE_LIMITED,
    "SmartThingsServerError": StErrorKind.SERVER,
}


def classify_st_error(exc: BaseException) -> StErrorKind:
    """Return what a SmartThings failure means."""
    for cls in type(exc).__mro__:
        if (kind := _BY_CLASS.get(cls.__name__)) is not None:
            return kind
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        if status in (401, 403):
            return StErrorKind.REFUSED
        if status == 402:
            return StErrorKind.QUOTA
        if status == 418:
            return StErrorKind.BLOCKED
        if status == 429:
            return StErrorKind.RATE_LIMITED
        if status >= 500:
            return StErrorKind.SERVER
        # The status is the whole answer: the text of a ClientResponseError
        # also carries the request, where "401" or "403" can appear anywhere.
        return StErrorKind.OTHER
    # Older pysmartthings and wrapped errors: the words are all there is.
    text = f"{type(exc).__name__} {exc}".lower()
    if "quota exceeded" in text:
        return StErrorKind.QUOTA
    if "flagged this client" in text:
        return StErrorKind.BLOCKED
    if any(marker in text for marker in ("forbidden", "unauthorized", "401", "403")):
        return StErrorKind.REFUSED
    if "rate limit" in text:
        return StErrorKind.RATE_LIMITED
    return StErrorKind.OTHER


def _backoffs(hass: HomeAssistant) -> dict[str, float]:
    return hass.data.setdefault(f"{DOMAIN}_st_backoff", {})


def set_st_backoff(hass: HomeAssistant, entry_id: str, seconds: float) -> None:
    """Leave SmartThings alone for this TV for ``seconds`` (keeps a longer one)."""
    until = time.monotonic() + seconds
    backoffs = _backoffs(hass)
    backoffs[entry_id] = max(backoffs.get(entry_id, 0.0), until)


def clear_st_backoff(hass: HomeAssistant, entry_id: str) -> None:
    """SmartThings answered: poll this TV normally again."""
    _backoffs(hass).pop(entry_id, None)


def st_backoff_active(hass: HomeAssistant, entry_id: str) -> bool:
    """Whether SmartThings calls for this TV are being held off."""
    until = _backoffs(hass).get(entry_id)
    return until is not None and time.monotonic() < until


def backoff_for(kind: StErrorKind) -> float | None:
    """How long a failure of this kind holds the cloud off, if at all."""
    if kind in (StErrorKind.QUOTA, StErrorKind.BLOCKED):
        return ST_ACCOUNT_BACKOFF
    if kind is StErrorKind.RATE_LIMITED:
        return ST_RATE_LIMIT_BACKOFF
    return None
