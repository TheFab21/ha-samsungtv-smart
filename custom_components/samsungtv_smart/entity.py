"""Base SamsungTV Entity."""

from __future__ import annotations

import html
from typing import Any

from homeassistant.const import (
    ATTR_CONNECTIONS,
    ATTR_IDENTIFIERS,
    ATTR_SW_VERSION,
    CONF_HOST,
    CONF_ID,
    CONF_MAC,
    CONF_NAME,
)
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import CONF_DEVICE_MODEL, CONF_DEVICE_NAME, CONF_DEVICE_OS, DOMAIN


class SamsungTVEntity(Entity):
    """Defines a base SamsungTV entity."""

    _attr_has_entity_name = True

    def __init__(self, config: dict[str, Any], entry_id: str) -> None:
        """Initialize the class."""
        self._name = config.get(CONF_NAME, config[CONF_HOST])
        self._mac = config.get(CONF_MAC)
        self._attr_unique_id = config.get(CONF_ID, entry_id)

        model = html.unescape(config.get(CONF_DEVICE_MODEL, "Samsung TV"))
        if dev_name := config.get(CONF_DEVICE_NAME):
            model = f"{model} ({html.unescape(dev_name)})"

        self._attr_device_info = DeviceInfo(
            manufacturer="Samsung Electronics",
            model=model,
            name=self._name,
        )
        if self.unique_id:
            self._attr_device_info[ATTR_IDENTIFIERS] = {(DOMAIN, self.unique_id)}
        # CONF_DEVICE_OS is stored verbatim from the TV's REST payload
        # (device.OS), so its type is whatever the firmware sent. The device
        # registry accepts only a string, and passing anything else has been
        # deprecated since Home Assistant 2026.x — it stops working in
        # 2026.12.0. Some payloads carry a list; join it rather than dropping
        # the information, and stringify anything else.
        if dev_os := config.get(CONF_DEVICE_OS):
            if isinstance(dev_os, (list, tuple, set)):
                dev_os = ", ".join(str(part) for part in dev_os)
            elif not isinstance(dev_os, str):
                dev_os = str(dev_os)
            if dev_os:
                self._attr_device_info[ATTR_SW_VERSION] = dev_os
        if self._mac:
            self._attr_device_info[ATTR_CONNECTIONS] = {
                (CONNECTION_NETWORK_MAC, self._mac)
            }
