"""Samsung Frame TV Art Mode switch entity."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from pysmartthings import Capability, Command

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_ID,
    CONF_NAME,
    CONF_PORT,
    CONF_TOKEN,
    STATE_OFF,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from . import async_get_samsungtv_api_key
from .api.art import SamsungTVAsyncArt
from .api.ipcontrol import (
    SamsungIPControl,
    SamsungIPControlAuthError,
    SamsungIPControlError,
)
from .art_mode_guard import ArtModeWriteSuppressed, guard_for
from .const import (
    AUTH_METHOD_OAUTH,
    CONF_AUTH_METHOD,
    CONF_ENABLE_IP_CONTROL,
    CONF_IP_CONTROL_ART_MODE,
    CONF_IP_CONTROL_TOKEN,
    CONF_IS_FRAME_TV,
    CONF_WS_NAME,
    DATA_ART_API,
    DATA_CFG,
    DEFAULT_PORT,
    DOMAIN,
    WS_PREFIX,
    ip_control_port,
)

_LOGGER = logging.getLogger(__name__)


class _DeviceLoggerAdapter(logging.LoggerAdapter):
    """Prefix every log line with the TV's host so multi-TV logs can be told apart."""

    def process(self, msg, kwargs):
        return f"[{self.extra['host']}] {msg}", kwargs


# Poll the switch entities every 5s so the Art Mode / power switches track the
# TV as responsively as the SmartThings cloud poll. async_update only reads the
# media_player's already-published art_mode_status attribute (no extra TV
# network call in the normal case), so the short interval is cheap. Without
# this override Home Assistant falls back to its 30s default scan interval,
# which is what made the Art Mode switch feel sluggish on TVs where IP Control
# is disabled (no faster push path).
SCAN_INTERVAL = timedelta(seconds=5)

COMPONENT_MAIN = "main"  # SmartThings component

# Time to wait for TV to be ready after WOL
TV_STARTUP_DELAY = 8  # seconds


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Samsung Frame Art Mode switch from config entry."""
    config = hass.data[DOMAIN][entry.entry_id][DATA_CFG]
    host = config[CONF_HOST]
    port = config.get(CONF_PORT, DEFAULT_PORT)
    token = config.get(CONF_TOKEN)
    ws_name = config.get(CONF_WS_NAME, "HomeAssistant")

    # Get device unique ID - must match entity.py logic for device grouping
    device_unique_id = config.get(CONF_ID, entry.entry_id)

    # Get device name from config or entry title, fallback to host
    device_name = config.get(CONF_NAME) or entry.title or host

    session = async_get_clientsession(hass)

    # SmartThings config (needed for power off via Command.OFF)
    from .const import CONF_API_KEY, CONF_DEVICE_ID, CONF_OAUTH_TOKEN

    api_key = config.get(CONF_API_KEY)
    device_id = config.get(CONF_DEVICE_ID)
    auth_method = config.get(CONF_AUTH_METHOD)
    if auth_method == AUTH_METHOD_OAUTH and not api_key:
        oauth_token = config.get(CONF_OAUTH_TOKEN)
        if oauth_token and isinstance(oauth_token, dict):
            api_key = oauth_token.get("access_token")

    entities = []

    # Power switch is always created.
    # If SmartThings is configured, turn_off uses Command.OFF (works in Art Mode too).
    # Without SmartThings, turn_off falls back to media_player which handles Art Mode
    # natively via KEY_POWER over WebSocket.
    entities.append(
        SamsungTVPowerSwitch(
            hass=hass,
            entry=entry,
            device_id=device_id if (api_key and device_id) else None,
            device_name=device_name,
            session=session,
            device_unique_id=device_unique_id,
            host=host,
        )
    )
    _LOGGER.debug(
        "Power switch created for %s (SmartThings: %s)",
        device_name,
        "yes" if (api_key and device_id) else "no — WebSocket fallback",
    )

    # Reuse the shared Art API instance if present (created in __init__.py),
    # otherwise create a fallback one. Either way, decide whether to create the
    # Art Mode switch from the actual Frame-TV support check below — NOT from
    # whether the shared instance happens to exist: it is now created for every
    # TV (Frame or not) before platforms load, so its presence no longer implies
    # Frame support.
    art_api = hass.data[DOMAIN][entry.entry_id].get(DATA_ART_API)
    if art_api is None:
        art_api = SamsungTVAsyncArt(
            host=host,
            port=port,
            token=token,
            session=session,
            timeout=5,
            name=f"{WS_PREFIX} {ws_name} Art",
        )

    # Use persisted flag if available, otherwise probe live
    is_frame_tv_cached = entry.data.get(CONF_IS_FRAME_TV, False)
    if is_frame_tv_cached:
        _LOGGER.debug("Frame TV flag found for %s, skipping live check in switch", host)
        is_supported = True
    else:
        try:
            async with asyncio.timeout(5):
                is_supported = await art_api.supported()
        except asyncio.TimeoutError:
            _LOGGER.debug("Timeout checking Frame TV support for %s", host)
            is_supported = False
        except Exception as ex:
            _LOGGER.debug("Frame TV support check failed: %s", ex)
            is_supported = False

    if not is_supported:
        _LOGGER.info(
            "Frame TV art mode not supported on %s - art mode switch not created",
            host,
        )
    else:
        # Store for later use (no-op when already the shared instance)
        hass.data[DOMAIN][entry.entry_id][DATA_ART_API] = art_api
        # Add Art Mode switch
        entities.append(
            FrameArtModeSwitch(
                hass, entry, art_api, device_name, host, device_unique_id
            )
        )
        _LOGGER.info("Frame Art Mode switch created for %s", device_name)

    # Create the switch entities
    if entities:
        async_add_entities(entities)


class FrameArtModeSwitch(SwitchEntity):
    """Switch to control Samsung Frame TV Art Mode."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_has_entity_name = True
    _attr_name = "Art Mode"
    _attr_icon = "mdi:palette"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        art_api: SamsungTVAsyncArt,
        device_name: str,
        host: str,
        device_unique_id: str,
    ) -> None:
        """Initialize the Art Mode switch."""
        self._hass = hass
        self._entry = entry
        self._art_api = art_api
        self._device_name = device_name
        self._host = host
        self._log = _DeviceLoggerAdapter(_LOGGER, {"host": host or device_name})
        self._device_unique_id = device_unique_id
        self._attr_unique_id = f"{entry.entry_id}_art_mode_switch"
        self._attr_is_on = None
        self._available = True
        self._updating = False
        # Optimistic-hold guard: after an explicit user toggle the
        # media_player's art_mode_status lags (~5s poll + Art WebSocket
        # propagation), so for a short window we keep the value we just set and
        # ignore any *contradicting* stale reading (which previously snapped the
        # switch back and made it look like a ~25s refresh).
        self._optimistic_value: bool | None = None
        self._optimistic_until: float = 0.0
        self._media_player_entity_id: str | None = None
        self._ip_control: SamsungIPControl | None = None
        self._ip_control_token: str | None = None
        # Set when an Art Mode ON was requested but the TV was off and never
        # became reachable (off the network). We don't hammer it; instead we
        # re-apply once the media_player reports the TV back online. Cleared by
        # a successful turn-on, or by an explicit turn-off (art no longer
        # wanted).
        self._pending_art_on = False

    def _get_ip_control(self) -> SamsungIPControl | None:
        """Return an IP Control client if paired AND enabled, else None.

        Token is read live from entry.data (pairing takes effect with no
        reload), and the channel must be enabled in the options.
        """
        if not self._host:
            return None
        token = self._entry.data.get(CONF_IP_CONTROL_TOKEN)
        if not token or not self._entry.options.get(CONF_ENABLE_IP_CONTROL, True):
            return None
        if self._ip_control is None or self._ip_control_token != token:
            self._ip_control = SamsungIPControl(
                self._hass,
                self._host,
                port=ip_control_port(self._entry.data),
                token=token,
            )
            self._ip_control_token = token
        return self._ip_control

    def _ip_control_art_mode(self) -> bool:
        """True when art mode may use IP Control at all (default: off)."""
        return self._entry.options.get(CONF_IP_CONTROL_ART_MODE, False)

    async def _set_artmode(self, turn_on: bool):
        """Set Art Mode via IP Control (primary), WebSocket as fallback.

        On a healthy Frame, IP Control ``artModeControl`` reliably flips the
        panel in both directions (confirmed on QE55LS03D: artModeOn -> Ambient,
        artModeOff -> a real picture mode). It is preferred over the WebSocket
        art channel, whose ``set_artmode`` returns None/False or "times out but
        a broadcast confirms" — a false success that doesn't move the panel. On
        any IP Control failure (not paired, transport, auth) we fall back to the
        WebSocket so behaviour is never worse than before. The caller verifies
        the resulting state afterwards.

        ``CONF_IP_CONTROL_ART_MODE`` gates this path, reads AND writes. It
        used to gate only the ``artModeControl`` getter, on the reasoning that
        the explicit artModeOn/artModeOff command stays reliable even on a TV
        whose getter has wedged. That left the setting dishonest: our own
        documentation tells users to disable it because the IP Control art-mode
        path "can break Art Mode entirely and may need a factory reset (seen on
        QE55LS03D fw 2123)", yet a user who did exactly that still had every
        art-mode toggle sent to the TV over JSON-RPC. Whatever that firmware
        fault really is, "off" must mean no artModeControl traffic at all —
        switching then falls back to the WebSocket art channel, as the
        documentation already claims it does.
        """
        guard = guard_for(self._hass.data[DOMAIN][self._entry.entry_id])

        # 1. Panel truth before writing. getTVStates.pictureMode is a plain
        # getter that needs only IP Control to be paired — not the art-mode
        # option — and it is independent of the artModeControl flag (which can
        # wedge) and of the WebSocket art channel (which can go stale). If the
        # panel already shows what was asked for, the request is satisfied and
        # writing again is exactly the loop we are guarding against.
        panel = await self._panel_shows_art()
        if panel is turn_on:
            self._log.warning(
                "Art Mode %s requested for %s but the panel already shows it — "
                "the art-mode reading was stale; not writing",
                "ON" if turn_on else "OFF",
                self._device_name,
            )
            guard.record_verified(turn_on)
            return True

        # 2. Cooldown: refuse to repeat a same-intent write that did not take.
        guard.check(turn_on)  # raises ArtModeWriteSuppressed

        client = self._get_ip_control() if self._ip_control_art_mode() else None
        if client is not None:
            try:
                if turn_on:
                    await client.async_set_art_mode_on()
                else:
                    await client.async_set_art_mode_off()
                self._log.debug(
                    "Art Mode set to %s via IP Control for %s",
                    turn_on,
                    self._device_name,
                )
            except SamsungIPControlError as ex:
                self._log.debug(
                    "IP Control art-mode set failed (%s); falling back to "
                    "WebSocket for %s",
                    ex,
                    self._device_name,
                )
            else:
                # 4. Read the panel back instead of trusting the accepted
                # command: "COMPLETED" does not move a panel.
                await asyncio.sleep(2)
                after = await self._panel_shows_art()
                if after is turn_on:
                    guard.record_verified(turn_on)
                    return True
                guard.record_unverified(turn_on)
                if after is None:
                    self._log.debug(
                        "Art Mode %s for %s accepted via IP Control but the panel "
                        "could not be read back",
                        turn_on,
                        self._device_name,
                    )
                    return True
                self._log.warning(
                    "Art Mode %s for %s was accepted via IP Control but the panel "
                    "did not change — not retrying within the cooldown",
                    "ON" if turn_on else "OFF",
                    self._device_name,
                )
                return False

        result = await self._art_api.set_artmode(turn_on)
        if result:
            # The WebSocket path has no panel read-back here; the caller's
            # get_artmode() check verifies it and clears this.
            guard.record_unverified(turn_on)
        return result

    async def _panel_shows_art(self) -> bool | None:
        """getTVStates.pictureMode == "Ambient", or None when unreadable."""
        client = self._get_ip_control()
        if client is None:
            return None
        try:
            return await client.async_panel_shows_art()
        except SamsungIPControlError:
            return None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to link this entity to the TV device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    @property
    def is_on(self) -> bool | None:
        """Return True if Art Mode is on."""
        return self._attr_is_on

    def _get_media_player_entity_id(self) -> str | None:
        """Get the media_player entity_id for this TV."""
        if self._media_player_entity_id:
            return self._media_player_entity_id

        # Find media_player entity for this config entry using the correct API
        entity_registry = er.async_get(self._hass)
        for entity in entity_registry.entities.values():
            if (
                entity.config_entry_id == self._entry.entry_id
                and entity.domain == "media_player"
            ):
                self._media_player_entity_id = entity.entity_id
                return entity.entity_id

        return None

    async def _is_tv_on(self) -> bool:
        """Check if TV is currently on OR already in Art Mode.

        A Frame TV in Art Mode reports media_player state as "off", but the
        screen is physically on. Treat that as on, consistent with
        SamsungTVPowerSwitch.is_on logic.

        "unknown" means the WebSocket connection is not yet established —
        typically the TV is booting up. Treat it as on so that Art Mode
        activation is not blocked during the startup transient.

        "unavailable" (or no media_player at all) says nothing about the TV
        itself — e.g. a SmartThings cloud failure crashes the media_player
        update while the TV keeps working locally. Ask the TV directly over
        the local REST API instead of assuming it is off.
        """
        entity_id = self._get_media_player_entity_id()
        state = self._hass.states.get(entity_id) if entity_id else None

        if state is None or state.state == "unavailable":
            try:
                return await self._art_api.on()
            except Exception:  # pylint: disable=broad-except
                return False

        # TV is clearly on (watching TV, idle, paused...) or still starting up
        if state.state != STATE_OFF:
            return True

        # media_player says "off" — but Art Mode may be active
        return state.attributes.get("art_mode_status") == "on"

    async def _turn_on_tv(self) -> bool:
        """Turn on the TV using media_player service."""
        entity_id = self._get_media_player_entity_id()
        if not entity_id:
            self._log.warning(
                "Could not find media_player entity for %s", self._device_name
            )
            return False

        self._log.info("Turning on TV %s before activating Art Mode", entity_id)

        try:
            await self._hass.services.async_call(
                "media_player",
                "turn_on",
                {"entity_id": entity_id},
                blocking=True,
            )
            return True
        except Exception as ex:
            self._log.debug("Failed to turn on TV %s: %s", entity_id, ex)
            return False

    async def _wait_for_tv_ready(self, max_wait: int = 15) -> bool:
        """Wait for TV to be ready after turning on."""
        self._log.debug("Waiting for TV to be ready (max %ds)...", max_wait)

        for i in range(max_wait):
            await asyncio.sleep(1)

            # Try to connect to Art API
            try:
                async with asyncio.timeout(3):
                    is_supported = await self._art_api.supported()
                    if is_supported:
                        self._log.debug("TV ready after %d seconds", i + 1)
                        return True
            except Exception:
                pass

            self._log.debug("TV not ready yet, waiting... (%d/%d)", i + 1, max_wait)

        self._log.warning("TV did not become ready within %d seconds", max_wait)
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn Art Mode on."""
        self._log.debug("Turning Art Mode ON for %s", self._device_name)
        # A fresh explicit attempt supersedes any earlier deferred one. Keep
        # whether one was outstanding: it distinguishes the first failure to
        # reach a TV from the repeats of a caller that keeps asking.
        was_pending = self._pending_art_on
        self._pending_art_on = False

        # Short-circuit: if already in Art Mode, nothing to do.
        # Avoids useless set_artmode(True) calls that the TV may silently
        # reject (returning None), triggering 3 retries and a WARNING log.
        entity_id = self._get_media_player_entity_id()
        if entity_id:
            state = self._hass.states.get(entity_id)
            if state and state.attributes.get("art_mode_status") == "on":
                self._log.debug(
                    "Art Mode already ON for %s, skipping activation",
                    self._device_name,
                )
                self._attr_is_on = True
                self._available = True
                self.async_write_ha_state()
                return

        # Check if TV is on
        tv_is_on = await self._is_tv_on()

        if not tv_is_on:
            self._log.info("TV is off, turning it on first...")

            # Turn on TV
            if not await self._turn_on_tv():
                self._log.warning(
                    "Cannot activate Art Mode on %s: TV is not reachable",
                    self._device_name,
                )
                return

            # Wait for TV to be ready. If it never becomes reachable within the
            # window, it did not respond to power-on — it is off the network
            # (deep standby / dropped Wi-Fi), not merely cold-booting. Bail out
            # instead of "trying anyway": the following 8s stabilise wait plus
            # the 5x art-mode retry loop would otherwise thrash for ~60s against
            # a TV that cannot answer (observed after a spurious art-mode-on
            # command on an off Salon TV — issue #116 follow-up).
            tv_was_off = True
            if not await self._wait_for_tv_ready(max_wait=20):
                # Say what actually happened: the power-on WAS sent (WOL, or
                # KEY_POWER / IP Control powerOn depending on the configured
                # method) and the TV did not answer within 20 s. Only the art
                # mode part is deferred.
                #
                # Repeat calls are logged at INFO. An automation that asks
                # every few minutes re-enters here each time — and because the
                # top of this method clears _pending_art_on, each call also
                # cancels the previous deferral — so a TV that wakes late
                # produced one WARNING per attempt for an entirely normal
                # morning (11 in 50 minutes on a TV whose usual wake time is
                # 05:51-08:10, #248). The first is worth a warning; the rest
                # are the deferral working.
                log = self._log.info if was_pending else self._log.warning
                log(
                    "Art Mode ON for %s: the TV did not answer within 20s of "
                    "the power-on request (likely in deep standby / off the "
                    "network) — art mode deferred until it is back online",
                    self._device_name,
                )
                # Defer instead of hammering: re-apply once the media_player
                # reports the TV reachable again (handled in the state listener).
                self._pending_art_on = True
                self._set_optimistic(False)
                self._available = True
                self.async_write_ha_state()
                return

            # Additional delay for TV Art subsystem to stabilize after boot.
            # supported() returns True quickly (WebSocket reachable) but
            # set_artmode() may still fail for several seconds while the Art
            # subsystem initialises. 8s covers cold-boot scenarios reliably.
            self._log.debug(
                "Waiting 8s for Art subsystem to stabilize after power-on..."
            )
            await asyncio.sleep(8)
        else:
            tv_was_off = False

        # Use more retries with longer delays when coming from a cold boot,
        # because the Art WebSocket may still be settling.
        max_retries = 5 if tv_was_off else 3
        retry_delay = 3 if tv_was_off else 2

        for attempt in range(max_retries):
            try:
                async with asyncio.timeout(10):
                    self._log.debug(
                        "Art Mode ON attempt %d/%d for %s",
                        attempt + 1,
                        max_retries,
                        self._device_name,
                    )
                    result = await self._set_artmode(True)
                    if result:
                        # Set state immediately for responsive UI and hold it
                        # against the lagging media_player reading.
                        self._set_optimistic(True)
                        self._available = True
                        self.async_write_ha_state()
                        self._log.info("Art Mode turned ON for %s", self._device_name)

                        # Keep the optimistic state and let the media_player
                        # state-change listener correct it authoritatively once
                        # art_mode_status actually flips. A self-triggered
                        # async_update() here read the media_player's
                        # art_mode_status before it had caught up (~5s poll +
                        # WebSocket propagation), so it overwrote the fresh
                        # optimistic True with a stale False — making the switch
                        # snap back off and only recover ~25s later.
                        return  # Success, exit
                    else:
                        # set_artmode returned None/False — this can happen when
                        # the WebSocket is not connected (returns None) or when
                        # the TV is already in Art Mode (no-op, no response).
                        # Before retrying, check actual state: if Art Mode is
                        # already on, we're done.
                        self._log.debug(
                            "Art Mode set_artmode returned None/False on attempt %d,"
                            " checking actual state...",
                            attempt + 1,
                        )
                        try:
                            actual = await self._art_api.get_artmode()
                            if actual == "on":
                                self._log.info(
                                    "Art Mode is already ON for %s"
                                    " (confirmed by get_artmode)",
                                    self._device_name,
                                )
                                guard_for(
                                    self._hass.data[DOMAIN][self._entry.entry_id]
                                ).record_verified(True)
                                self._attr_is_on = True
                                self._available = True
                                self.async_write_ha_state()
                                return
                        except Exception:
                            pass

                        if attempt < max_retries - 1:
                            self._log.debug("Retrying in %d seconds...", retry_delay)
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff

            except ArtModeWriteSuppressed as ex:
                # The same write was just made and did not take. Retrying is
                # the loop this guard exists to stop; say so once and stop.
                self._log.warning("Art Mode for %s: %s", self._device_name, ex)
                self.async_write_ha_state()
                return
            except asyncio.TimeoutError:
                self._log.debug("Timeout on attempt %d/%d", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
            except Exception as ex:
                self._log.debug(
                    "Error on attempt %d/%d: %s", attempt + 1, max_retries, ex
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2

        # All retries failed
        self._log.warning(
            "Failed to turn Art Mode ON for %s after %d attempts",
            self._device_name,
            max_retries,
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn Art Mode off (switch to normal TV mode)."""
        self._log.debug("Turning Art Mode OFF for %s", self._device_name)
        # Art no longer wanted — drop any deferred re-apply.
        self._pending_art_on = False

        # Check if TV is on
        tv_is_on = await self._is_tv_on()

        if not tv_is_on:
            self._log.debug("TV is already off, Art Mode is already off")
            self._attr_is_on = False
            self.async_write_ha_state()
            return

        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                async with asyncio.timeout(10):
                    self._log.debug(
                        "Art Mode OFF attempt %d/%d for %s",
                        attempt + 1,
                        max_retries,
                        self._device_name,
                    )
                    result = await self._set_artmode(False)
                    if result:
                        # Set state immediately for responsive UI and hold it
                        # against the lagging media_player reading.
                        self._set_optimistic(False)
                        self._available = True
                        self.async_write_ha_state()
                        self._log.info("Art Mode turned OFF for %s", self._device_name)

                        # Keep the optimistic state and let the media_player
                        # state-change listener correct it authoritatively once
                        # art_mode_status actually flips. A self-triggered
                        # async_update() here read the media_player's
                        # art_mode_status before it had caught up, overwriting
                        # the fresh optimistic value with a stale one (the
                        # mirror of the turn-on slow-refresh bug).
                        return  # Success, exit
                    else:
                        self._log.debug(
                            "Art Mode set_artmode(False) returned None/False on attempt %d",
                            attempt + 1,
                        )
                        if attempt < max_retries - 1:
                            self._log.debug("Retrying in %d seconds...", retry_delay)
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 2

            except ArtModeWriteSuppressed as ex:
                # The same write was just made and did not take. Retrying is
                # the loop this guard exists to stop; say so once and stop.
                self._log.warning("Art Mode for %s: %s", self._device_name, ex)
                self.async_write_ha_state()
                return
            except asyncio.TimeoutError:
                self._log.debug("Timeout on attempt %d/%d", attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
            except Exception as ex:
                self._log.debug(
                    "Error on attempt %d/%d: %s", attempt + 1, max_retries, ex
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2

        # All retries failed
        self._log.warning(
            "Failed to turn Art Mode OFF for %s after %d attempts",
            self._device_name,
            max_retries,
        )
        self.async_write_ha_state()

    # How long to trust an explicit user toggle over a contradicting
    # media_player reading (its art_mode_status lags ~5s poll + WS propagation,
    # observed up to ~40s on a 2024 Frame).
    _OPTIMISTIC_HOLD_SECONDS = 45

    def _set_optimistic(self, value: bool) -> None:
        """Record an explicit toggle so stale readings don't override it."""
        self._attr_is_on = value
        self._optimistic_value = value
        self._optimistic_until = time.monotonic() + self._OPTIMISTIC_HOLD_SECONDS

    def _optimistic_blocks(self, incoming: bool) -> bool:
        """Return True if ``incoming`` contradicts a still-valid optimistic value.

        A reading that *matches* the optimistic value clears the hold (the
        authoritative source caught up); a contradicting one within the window
        is ignored (it is the lagging pre-toggle value).
        """
        if self._optimistic_value is None:
            return False
        if time.monotonic() >= self._optimistic_until:
            self._optimistic_value = None
            return False
        if incoming == self._optimistic_value:
            self._optimistic_value = None
            return False
        return True

    async def async_update(self) -> None:
        """Update the Art Mode state."""
        if self._updating:
            return

        self._updating = True
        try:
            # FIRST: Check if TV is powered off
            tv_is_on = await self._is_tv_on()

            if not tv_is_on:
                # TV is off, Art Mode must be off too
                self._log.debug("TV is off, setting Art Mode to off")
                self._attr_is_on = False
                self._available = True
                return

            # TV is on — mirror the canonical Art Mode status from the
            # media_player. Its art_mode_status attribute is the integration's
            # authoritative value (IP Control getTVStates.pictureMode, power-
            # gated). The WebSocket get_artmode() poll cannot be trusted on
            # 2024+ Frames: it returns "on" even while a real HDMI input is
            # displayed (the TV's internal art flag is stuck), which is what
            # pinned this switch on. Only fall back to the WS read if the
            # media_player attribute is not yet available (e.g. at startup).
            entity_id = self._get_media_player_entity_id()
            mp_state = self._hass.states.get(entity_id) if entity_id else None
            if mp_state is not None and "art_mode_status" in mp_state.attributes:
                incoming = mp_state.attributes.get("art_mode_status") == "on"
                self._available = True
                if self._optimistic_blocks(incoming):
                    self._log.debug(
                        "Ignoring stale art_mode_status=%s (holding optimistic %s)",
                        incoming,
                        self._optimistic_value,
                    )
                else:
                    self._attr_is_on = incoming
                    self._log.debug("Art Mode state updated: %s", self._attr_is_on)
            else:
                async with asyncio.timeout(8):
                    art_mode = await self._art_api.get_artmode()
                    if art_mode is not None:
                        self._attr_is_on = art_mode == "on"
                        self._available = True
                        self._log.debug(
                            "Art Mode state updated (WS fallback): %s",
                            self._attr_is_on,
                        )
                    else:
                        self._log.debug("Could not get Art Mode state")
        except asyncio.TimeoutError:
            self._log.debug("Timeout updating Art Mode state")
            # Don't mark as unavailable on timeout - TV might be off
        except Exception as ex:
            self._log.debug("Error updating Art Mode state: %s", ex)
        finally:
            self._updating = False

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to Home Assistant."""
        # Schedule initial state update
        self.hass.async_create_background_task(
            self.async_update(),
            f"frame_art_switch_initial_update_{self._entry.entry_id}",
        )

        # Subscribe to media_player state changes for responsive UI updates.
        # When media_player toggles between on/off or art_mode_status changes,
        # re-read actual Art Mode state immediately instead of waiting for the
        # next polling cycle (up to 30s).
        entity_id = self._get_media_player_entity_id()
        if entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self._hass,
                    [entity_id],
                    self._handle_media_player_state_change,
                )
            )

    @callback
    def _handle_media_player_state_change(self, event) -> None:
        """React to media_player state changes and refresh Art Mode state."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        art_status = new_state.attributes.get("art_mode_status")

        # Deferred re-apply: a previous Art Mode ON was aborted because the TV
        # was off the network. Re-apply now that it is reachable again — but
        # only on a real edge to avoid loops. If art is already on, the request
        # is already satisfied.
        if self._pending_art_on:
            if art_status == "on":
                self._pending_art_on = False
            elif new_state.state not in (STATE_OFF, "unavailable", "unknown"):
                self._pending_art_on = False
                self._log.info(
                    "TV %s back online — re-applying deferred Art Mode ON",
                    self._device_name,
                )
                self._hass.async_create_background_task(
                    self.async_turn_on(),
                    f"art_mode_deferred_on_{self._entry.entry_id}",
                )
                return
        if art_status in ("on", "off"):
            # Mirror the media_player's authoritative art_mode_status directly,
            # including when the TV is ON but no longer in Art Mode (e.g. an app
            # was just launched). Without this the switch kept its previous
            # value and lingered until its own poll. But honour a recent
            # explicit toggle: this attribute lags after a switch action, and
            # the stale pre-toggle value used to snap the switch back.
            incoming = art_status == "on"
            if self._optimistic_blocks(incoming):
                self._log.debug(
                    "Ignoring stale media_player art_mode_status=%s"
                    " (holding optimistic %s)",
                    art_status,
                    self._optimistic_value,
                )
                return
            self._attr_is_on = incoming
        elif new_state.state == STATE_OFF:
            self._attr_is_on = False
        elif new_state.state in ("unknown", "unavailable"):
            # "unknown": TV is booting up — WebSocket not yet established.
            # "unavailable": the media_player update failed (e.g. SmartThings
            # cloud error) — that says nothing about the TV itself.
            # Schedule a deferred re-check so the switch reflects the actual
            # Art Mode state (asking the TV directly), without guessing here.
            self._hass.async_create_background_task(
                self._deferred_state_refresh(delay=8),
                f"art_mode_switch_deferred_refresh_{self._entry.entry_id}",
            )
            return  # Don't write state yet — keep last known value
        self._available = True
        self.async_write_ha_state()

    async def _deferred_state_refresh(self, delay: int = 8) -> None:
        """Wait for TV to settle after power-on, then refresh Art Mode state."""
        await asyncio.sleep(delay)
        await self.async_update()
        self.async_write_ha_state()


class SamsungTVPowerSwitch(SwitchEntity):
    """Switch for turning Samsung TV on/off.

    Delegates all commands to the media_player entity so there is a single
    source of truth for the TV power state (WOL, WebSocket, SmartThings are
    all handled there).  This switch is a pure UI convenience wrapper.
    """

    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_has_entity_name = True
    _attr_translation_key = "power"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        device_id: str | None,
        device_name: str,
        session,
        device_unique_id: str,
        host: str | None = None,
    ) -> None:
        """Initialize the power switch."""
        self.hass = hass
        self._entry = entry
        self._device_id = device_id  # None if SmartThings not configured
        self._session = session
        self._device_name = device_name
        self._device_unique_id = device_unique_id
        self._host = host
        self._log = _DeviceLoggerAdapter(_LOGGER, {"host": host or device_name})
        self._attr_unique_id = f"{entry.entry_id}_power"
        self._media_player_entity_id: str | None = None
        # Optimistic override — set by turn_on/turn_off so the UI flips
        # immediately. Holds until the media_player reaches the matching state
        # (or the safety timeout fires). Replaces the previous "clear on first
        # state change" behaviour, which caused the switch to flicker during
        # the multi-second wake-up window after a WOL fallback.
        self._optimistic_state: bool | None = None
        self._optimistic_cancel: callable | None = None
        # IP Control client (lazy) and the token it was built with, so we can
        # rebuild it transparently if the token changes (e.g. after re-pairing).
        self._ip_control: SamsungIPControl | None = None
        self._ip_control_token: str | None = None

    # -- optimistic state machine -------------------------------------------

    OPTIMISTIC_TIMEOUT = 30.0  # seconds — safety fallback if media_player never
    # catches up (network issue, TV unresponsive, …)

    def _set_optimistic(self, target: bool) -> None:
        """Set the optimistic state and schedule a safety timeout.

        Cancels any previous pending timeout so back-to-back actions don't leak
        callbacks. The state is cleared either when media_player reaches the
        matching value (handled in _handle_media_player_state_change) or when
        the safety timeout fires.
        """
        self._cancel_optimistic_timeout()
        self._optimistic_state = target
        self._optimistic_cancel = async_call_later(
            self.hass, self.OPTIMISTIC_TIMEOUT, self._optimistic_safety_clear
        )
        self.async_write_ha_state()

    def _cancel_optimistic_timeout(self) -> None:
        if self._optimistic_cancel is not None:
            self._optimistic_cancel()
            self._optimistic_cancel = None

    @callback
    def _optimistic_safety_clear(self, _now=None) -> None:
        """Drop the optimistic state if media_player never caught up."""
        self._optimistic_cancel = None
        if self._optimistic_state is not None:
            self._log.debug(
                "Power switch: optimistic state timed out — falling back to "
                "media_player state"
            )
            self._optimistic_state = None
            self.async_write_ha_state()

    @staticmethod
    def _state_to_is_on(state) -> bool | None:
        """Mirror the is_on logic on an arbitrary state object."""
        if state is None:
            return None
        if state.state not in (STATE_OFF, "unavailable", "unknown"):
            return True
        if state.state == STATE_OFF:
            return state.attributes.get("art_mode_status") == "on"
        return None

    def _get_ip_control(self) -> SamsungIPControl | None:
        """Return an IP Control client if paired AND enabled, else None.

        The token is read live from entry.data so that pairing (done via the
        options flow) takes effect immediately, with no reload required. The
        channel must also be enabled in the options, consistent with every
        other IP Control consumer (media_player, art mode switch, button).
        """
        if not self._host:
            return None
        token = self._entry.data.get(CONF_IP_CONTROL_TOKEN)
        if not token or not self._entry.options.get(CONF_ENABLE_IP_CONTROL, True):
            return None
        if self._ip_control is None or self._ip_control_token != token:
            self._ip_control = SamsungIPControl(
                self.hass,
                self._host,
                port=ip_control_port(self._entry.data),
                token=token,
            )
            self._ip_control_token = token
        return self._ip_control

    async def _get_st_client(self):
        """Get SmartThings client with current (possibly refreshed) token."""
        from pysmartthings import SmartThings

        from .const import CONF_API_KEY, CONF_OAUTH_TOKEN

        api_key = await async_get_samsungtv_api_key(self.hass, self._entry)
        if not api_key:
            config = self.hass.data[DOMAIN][self._entry.entry_id][DATA_CFG]
            api_key = config.get(CONF_API_KEY)
            if not api_key:
                oauth_token = config.get(CONF_OAUTH_TOKEN)
                if oauth_token and isinstance(oauth_token, dict):
                    api_key = oauth_token.get("access_token")
        if not api_key:
            self._log.warning("Power switch: no SmartThings API key available")
            return None
        st_client = SmartThings(session=self._session)
        st_client.authenticate(api_key)
        return st_client

    def _get_media_player_entity_id(self) -> str | None:
        """Get the media_player entity_id for this TV (cached after first lookup)."""
        if self._media_player_entity_id:
            return self._media_player_entity_id

        entity_registry = er.async_get(self.hass)
        for entity in entity_registry.entities.values():
            if (
                entity.config_entry_id == self._entry.entry_id
                and entity.domain == "media_player"
            ):
                self._media_player_entity_id = entity.entity_id
                return entity.entity_id

        return None

    def _get_media_player_state(self):
        """Return the current HA state object of the media_player, or None."""
        entity_id = self._get_media_player_entity_id()
        if not entity_id:
            return None
        return self.hass.states.get(entity_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            name=self._device_name,
        )

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return "Power"

    @property
    def available(self) -> bool:
        """Available unless the media_player is truly unreachable."""
        state = self._get_media_player_state()
        if state is None:
            return False
        return state.state != "unavailable"

    @property
    def is_on(self) -> bool | None:
        """TV is physically on if media_player is on OR Art Mode is active.

        A Frame TV in Art Mode reports media_player state as "off", but the
        screen is physically on.  We treat that as on so that automations
        reading this switch never send a spurious turn_on to an already-lit TV.
        """
        # Return optimistic state immediately after user action, before
        # media_player has had time to update its own state.
        if self._optimistic_state is not None:
            return self._optimistic_state

        state = self._get_media_player_state()
        if state is None:
            return None
        # Clearly on (watching TV, idle, paused...)
        if state.state not in (STATE_OFF, "unavailable", "unknown"):
            return True
        # media_player says "off" — but Art Mode may be active
        if state.state == STATE_OFF:
            return state.attributes.get("art_mode_status") == "on"
        return False

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:power" if self.is_on else "mdi:power-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the TV on.

        Strategy:
        - IP Control paired → powerOn via JSON-RPC (explicit, future-proof if
          Samsung disables the WebSocket ports). Falls back on failure.
        - Otherwise (or on failure) → media_player.turn_on, which uses WOL /
          WebSocket as today.
        """
        self._set_optimistic(True)

        ip_control = self._get_ip_control()
        if ip_control is not None:
            try:
                await ip_control.async_power_on()
                self._log.debug("Power switch: TV turned on via IP Control")
                self.async_write_ha_state()
                return
            except SamsungIPControlAuthError as ex:
                self._log.warning(
                    "Power switch: IP Control token rejected (%s) — re-pair via "
                    "the integration options; falling back to media_player",
                    ex,
                )
            except SamsungIPControlError as ex:
                self._log.debug(
                    "Power switch: IP Control turn_on failed (%s), "
                    "falling back to media_player",
                    ex,
                )

        entity_id = self._get_media_player_entity_id()
        if not entity_id:
            self._log.warning(
                "Power switch: media_player entity not found for %s", self._device_name
            )
            return

        self._log.debug("Power switch: delegating turn_on to %s", entity_id)
        await self.hass.services.async_call(
            "media_player",
            "turn_on",
            {"entity_id": entity_id},
            blocking=True,
        )
        # State will update automatically when media_player changes
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the TV off.

        Priority:
        - SmartThings configured → Command.OFF (hardware-level, works regardless
          of TV state including Art Mode).
        - IP Control paired → powerOff via JSON-RPC (explicit, works from Art
          Mode, no cloud, future-proof if Samsung disables the WebSocket ports).
        - Otherwise → media_player.turn_off (KEY_POWER over WebSocket). Note that
          on Frame TVs this only toggles between viewing and Art Mode rather than
          issuing a true power-off — which is exactly why the paths above are
          preferred when available.
        """
        if self._device_id:
            # SmartThings path — bypasses HA state entirely
            try:
                st_client = await self._get_st_client()
                if st_client:
                    await st_client.execute_device_command(
                        self._device_id,
                        Capability.SWITCH,
                        Command.OFF,
                        COMPONENT_MAIN,
                    )
                    self._set_optimistic(False)
                    self._log.debug("Power switch: TV turned off via SmartThings")
                    return
                else:
                    self._log.warning(
                        "Power switch: SmartThings client unavailable, falling back"
                    )
            except Exception as ex:
                self._log.warning(
                    "Power switch: SmartThings turn_off failed (%s), falling back",
                    ex,
                )

        # IP Control path — explicit power-off, works from Art Mode.
        ip_control = self._get_ip_control()
        if ip_control is not None:
            try:
                await ip_control.async_power_off()
                self._set_optimistic(False)
                self._log.debug("Power switch: TV turned off via IP Control")
                return
            except SamsungIPControlAuthError as ex:
                self._log.warning(
                    "Power switch: IP Control token rejected (%s) — re-pair via "
                    "the integration options; falling back to WebSocket",
                    ex,
                )
            except SamsungIPControlError as ex:
                self._log.debug(
                    "Power switch: IP Control turn_off failed (%s), "
                    "falling back to WebSocket",
                    ex,
                )

        # WebSocket fallback —
        # media_player._turn_off() handles Art Mode via KEY_POWER natively
        entity_id = self._get_media_player_entity_id()
        if not entity_id:
            self._log.error(
                "Power switch: no media_player entity found for %s", self._device_name
            )
            return
        try:
            await self.hass.services.async_call(
                "media_player",
                "turn_off",
                {"entity_id": entity_id},
                blocking=True,
            )
            self._set_optimistic(False)
            self._log.debug("Power switch: TV turned off via media_player (WebSocket)")
        except Exception as ex:
            self._log.error("Power switch: WebSocket turn_off failed: %s", ex)

    @callback
    def _handle_media_player_state_change(self, event) -> None:
        """React to media_player state changes and push update immediately.

        The optimistic state is only cleared once media_player actually reaches
        the value we asked for. This prevents the switch from flickering
        through intermediate states (e.g. "off" -> "unavailable" -> "off" ->
        "on") while a WOL wake-up is in progress.
        """
        if self._optimistic_state is not None:
            new_state = event.data.get("new_state") if event.data else None
            actual = self._state_to_is_on(new_state)
            if actual is not None and actual == self._optimistic_state:
                self._cancel_optimistic_timeout()
                self._optimistic_state = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up the optimistic timeout when the entity goes away."""
        self._cancel_optimistic_timeout()

    async def async_added_to_hass(self) -> None:
        """Subscribe to media_player state changes when added to HA.

        Also publishes the current state right away: async_track_state_change_event
        only delivers *future* changes, so without an initial read the switch
        would keep whatever state it computed before media_player finished
        starting up — and only correct itself on the next media_player change
        (which is why a manual power on/off was needed to resync after a
        restart).
        """
        await super().async_added_to_hass()
        entity_id = self._get_media_player_entity_id()
        if entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [entity_id],
                    self._handle_media_player_state_change,
                )
            )
        # Publish the real state now instead of waiting for the first change.
        self.async_write_ha_state()
