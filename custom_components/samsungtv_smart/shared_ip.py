"""Shared IP status and fallback deadlines."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import math
import time
from typing import Any

from .const import (
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_TOKEN,
    DATA_IP_CONTROL_STATE_COORDINATOR,
    DOMAIN,
)


def state_coordinator(hass: Any, entry_id: str) -> Any | None:
    """Return shared status only while IP Control is enabled and paired."""
    entries = getattr(hass, "config_entries", None)
    if entries is None:
        return None
    entry = entries.async_get_entry(entry_id)
    if (
        entry is None
        or not entry.data.get(CONF_IP_CONTROL_TOKEN)
        or not entry.options.get(CONF_ENABLE_IP_CONTROL, True)
    ):
        return None
    return (
        hass.data.get(DOMAIN, {})
        .get(entry_id, {})
        .get(DATA_IP_CONTROL_STATE_COORDINATOR)
    )


def shared_field(coordinator: Any, field: str) -> tuple[Any, float] | None:
    """Return the field and its monotonic request time, or None if unavailable."""
    if not getattr(coordinator, "last_update_success", False):
        return None
    data = getattr(coordinator, "data", None)
    if not isinstance(data, dict) or data.get("powered_off"):
        return None
    tv = data.get("tv")
    observed = data.get("polled_at")
    if (
        not isinstance(tv, dict)
        or field not in tv
        or type(observed) not in (int, float)
        or not math.isfinite(observed)
    ):
        return None
    return tv[field], observed


class ReadDeadline:
    """Keep fallback reads within their original interval."""

    def __init__(self, interval: float, refresh: Callable[[], None]) -> None:
        self.interval = interval
        self._refresh = refresh
        self.observed_at: float | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._closed = False

    def accepts(self, observed_at: float) -> bool:
        return (
            not self._closed
            and 0 <= time.monotonic() - observed_at < self.interval
            and (self.observed_at is None or observed_at >= self.observed_at)
        )

    def current(self) -> bool:
        return self.observed_at is not None and self.accepts(self.observed_at)

    def record(self, observed_at: float) -> None:
        if self._closed:
            return
        if self.observed_at is not None and observed_at <= self.observed_at:
            return
        self.observed_at = observed_at
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(
            max(0.0, observed_at + self.interval - time.monotonic()), self._due
        )

    def _due(self) -> None:
        self._timer = None
        if not self._closed:
            self._refresh()

    def close(self) -> None:
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
