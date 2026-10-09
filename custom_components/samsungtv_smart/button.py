"""Samsung TV buttons: reboot (IP Control) and Art Mode brightness reset.

Exposes a single "Reboot TV" button, added only when the entry is paired for
IP Control (a CONF_IP_CONTROL_TOKEN is present) and the IP Control channel is
enabled in the options. Pressing it issues a reboot over the JSON-RPC channel
(port 1516), which is independent of the WebSocket channels — so it also
recovers a TV whose Art WebSocket has gone unresponsive.

The IP Control token survives the reboot, so no re-pairing is needed afterwards.
If the TV is off, it is powered on first and then rebooted. On an auth error the
IP Control persistent notification is raised; on success it is cleared.

On a Frame, a "Brightness Reset" button sends the art-app's reset_brightness
request, the same action as the Art Mode settings menu entry.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_ID, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.art import SamsungTVAsyncArt
from .api.ipcontrol import (
    SamsungIPControl,
    SamsungIPControlAuthError,
    SamsungIPControlError,
)
from .const import (
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_TOKEN,
    CONF_IS_FRAME_TV,
    DATA_ART_API,
    DATA_CFG,
    DOMAIN,
    ip_control_port,
)
from .token_notify import METHOD_IP_CONTROL, clear_token_problem, notify_token_problem

_LOGGER = logging.getLogger(__name__)


def _ip_control_active(entry: ConfigEntry) -> bool:
    """True when IP Control is paired AND enabled in the options."""
    return bool(entry.data.get(CONF_IP_CONTROL_TOKEN)) and entry.options.get(
        CONF_ENABLE_IP_CONTROL, True
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the reboot button (IP Control) and the Art brightness reset."""
    config = hass.data[DOMAIN][entry.entry_id][DATA_CFG]
    host = config[CONF_HOST]
    device_unique_id = config.get(CONF_ID, entry.entry_id)
    device_name = config.get(CONF_NAME) or entry.title or host

    entities: list[ButtonEntity] = []
    # Not paired, or the channel is disabled: no reboot path, so no button.
    # Pairing or re-enabling via the options flow reloads the entry and adds it
    # then.
    if _ip_control_active(entry):
        entities.append(
            SamsungTVRebootButton(hass, entry, host, device_unique_id, device_name)
        )

    art_api = hass.data[DOMAIN][entry.entry_id].get(DATA_ART_API)
    is_frame_tv = bool(entry.data.get(CONF_IS_FRAME_TV))
    if art_api is not None and not is_frame_tv:
        # Same probe as the art number entities when the flag is not persisted
        # yet (first setup).
        try:
            async with asyncio.timeout(5):
                is_frame_tv = await art_api.supported()
        except Exception:  # noqa: BLE001
            is_frame_tv = False
    if art_api is not None and is_frame_tv:
        entities.append(
            SamsungTVArtBrightnessResetButton(
                entry, art_api, device_unique_id, device_name
            )
        )

    if entities:
        async_add_entities(entities)


class SamsungTVRebootButton(ButtonEntity):
    """Button that reboots the TV via IP Control."""

    _attr_has_entity_name = True
    _attr_translation_key = "reboot"
    _attr_icon = "mdi:restart"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        host: str,
        device_unique_id: str,
        device_name: str,
    ) -> None:
        """Initialize the reboot button."""
        self.hass = hass
        self._entry_id = entry.entry_id
        self._host = host
        self._device_unique_id = device_unique_id
        self._device_name = device_name
        self._attr_unique_id = f"{entry.entry_id}_ip_control_reboot"

    @property
    def device_info(self) -> DeviceInfo:
        """Link this entity to the TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    @property
    def available(self) -> bool:
        """Available only while IP Control is paired and enabled (read live)."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return bool(entry and _ip_control_active(entry))

    def _device_title(self) -> str:
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        return entry.title if entry else (self._device_name or "this Samsung TV")

    async def async_press(self) -> None:
        """Reboot the TV via IP Control (powering it on first if it is off)."""
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        token = entry.data.get(CONF_IP_CONTROL_TOKEN) if entry else None
        if not token:
            raise HomeAssistantError(
                "IP Control is not paired for this TV — re-pair via the "
                "integration options first."
            )
        client = SamsungIPControl(
            self.hass,
            self._host,
            port=ip_control_port(entry.data if entry else {}),
            token=token,
        )
        try:
            power = await client.async_get_power_state()
            if power == "powerOff":
                _LOGGER.info(
                    "TV %s is powered off — powering on before reboot", self._host
                )
                await client.async_power_on()
                # Wait for the TV to accept the reboot command after power-on.
                await asyncio.sleep(7)
            await client.async_reboot()
        except SamsungIPControlAuthError as ex:
            notify_token_problem(
                self.hass, self._entry_id, METHOD_IP_CONTROL, self._device_title()
            )
            raise HomeAssistantError(
                f"IP Control token rejected while rebooting {self._host}: {ex}"
            ) from ex
        except SamsungIPControlError as ex:
            raise HomeAssistantError(
                f"Failed to reboot {self._host} via IP Control: {ex}"
            ) from ex
        # Reboot accepted — token is valid, so clear any stale notification.
        clear_token_problem(self.hass, self._entry_id, METHOD_IP_CONTROL)
        _LOGGER.info("Reboot requested for %s via IP Control", self._host)


class SamsungTVArtBrightnessResetButton(ButtonEntity):
    """Reset the Art Mode brightness to the TV's default."""

    _attr_has_entity_name = True
    _attr_translation_key = "art_brightness_reset"
    _attr_icon = "mdi:brightness-6"

    def __init__(
        self,
        entry: ConfigEntry,
        art_api: SamsungTVAsyncArt,
        device_unique_id: str,
        device_name: str,
    ) -> None:
        self._art_api = art_api
        self._device_unique_id = device_unique_id
        self._device_name = device_name
        self._attr_unique_id = f"{entry.entry_id}_art_brightness_reset"

    @property
    def device_info(self) -> DeviceInfo:
        """Link this entity to the TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    async def async_press(self) -> None:
        """Send reset_brightness over the art channel."""
        reply = await self._art_api.reset_brightness()
        if reply is None:
            raise HomeAssistantError(
                "The TV did not confirm the Art Mode brightness reset (is it on?)."
            )
        _LOGGER.info("Art Mode brightness reset to %s", reply.get("brightness_value"))
