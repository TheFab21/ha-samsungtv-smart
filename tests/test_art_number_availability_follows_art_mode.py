"""Art Mode number entities must republish availability when Art Mode changes.

Measured (192.168.1.161, 2026-10-05): a script switching Art Mode on and setting
number.samsung_hacs_art_mode_brightness 10 s later failed twice with
"Referenced entities … are missing or not currently available", and worked the
two other times. The entity's ``available`` is derived from the media_player's
art_mode_status (vetoed by the Frame Art sensor), but Home Assistant only
republishes it when the entity writes its state — on the number platform's
30 s poll — so whether a poll had landed in those 10 s decided the outcome.

The entities now follow the media_player and the Frame Art sensor.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.samsungtv_smart.const import DOMAIN  # noqa: E402
from custom_components.samsungtv_smart.number import (  # noqa: E402
    SamsungTVArtBrightnessNumber,
)
from homeassistant.helpers import entity_registry as er  # noqa: E402


def _setup(hass, *, register_sources=True):
    entry = MockConfigEntry(domain=DOMAIN, title="Samsung HACS", unique_id="tv-161")
    entry.add_to_hass(hass)
    ids = {}
    if register_sources:
        ids = _register_sources(hass, entry)
    art_api = MagicMock()
    art_api.get_brightness = AsyncMock(return_value={"value": 3})
    number = SamsungTVArtBrightnessNumber(hass, entry, art_api, "Samsung HACS", "tv")
    return entry, number, ids


def _register_sources(hass, entry):
    registry = er.async_get(hass)
    media_player = registry.async_get_or_create(
        "media_player", DOMAIN, "tv-161-mp", config_entry=entry
    )
    sensor = registry.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_frame_art", config_entry=entry
    )
    # Normal viewing: art off.
    hass.states.async_set(media_player.entity_id, "on", {"art_mode_status": "off"})
    hass.states.async_set(sensor.entity_id, "off")
    return {"media_player": media_player.entity_id, "sensor": sensor.entity_id}


async def _add(hass, number):
    platform = MockEntityPlatform(hass, domain="number", platform_name=DOMAIN)
    await platform.async_add_entities([number])
    await hass.async_block_till_done()


async def test_entering_art_mode_makes_the_slider_available_without_a_poll(hass):
    _, number, ids = _setup(hass)
    await _add(hass, number)
    assert hass.states.get(number.entity_id).state == "unavailable"

    # Art on: the media_player flips first, the Frame Art sensor follows on
    # its own 5 s poll. Until it does, its "off" vetoes availability.
    hass.states.async_set(ids["media_player"], "off", {"art_mode_status": "on"})
    await hass.async_block_till_done()
    assert hass.states.get(number.entity_id).state == "unavailable"

    hass.states.async_set(ids["sensor"], "on")
    await hass.async_block_till_done()
    state = hass.states.get(number.entity_id)
    assert state.state != "unavailable"
    # …and the live value was read on the way (TV 3 -> 30 %).
    number._art_api.get_brightness.assert_awaited()
    assert float(state.state) == 30


async def test_leaving_art_mode_makes_the_slider_unavailable_at_once(hass):
    _, number, ids = _setup(hass)
    hass.states.async_set(ids["media_player"], "off", {"art_mode_status": "on"})
    hass.states.async_set(ids["sensor"], "on")
    await _add(hass, number)
    assert hass.states.get(number.entity_id).state != "unavailable"

    hass.states.async_set(ids["media_player"], "on", {"art_mode_status": "off"})
    await hass.async_block_till_done()
    assert hass.states.get(number.entity_id).state == "unavailable"


async def test_unrelated_media_player_changes_do_not_reread_the_tv(hass):
    _, number, ids = _setup(hass)
    hass.states.async_set(ids["media_player"], "off", {"art_mode_status": "on"})
    hass.states.async_set(ids["sensor"], "on")
    await _add(hass, number)
    number._art_api.get_brightness.reset_mock()

    hass.states.async_set(
        ids["media_player"], "off", {"art_mode_status": "on", "media_title": "x"}
    )
    await hass.async_block_till_done()
    number._art_api.get_brightness.assert_not_awaited()


async def test_sources_registered_after_the_entity_are_picked_up_by_the_poll(hass):
    entry, number, _ = _setup(hass, register_sources=False)
    await _add(hass, number)
    ids = _register_sources(hass, entry)

    await number.async_update()  # the next platform poll
    hass.states.async_set(ids["media_player"], "off", {"art_mode_status": "on"})
    hass.states.async_set(ids["sensor"], "on")
    await hass.async_block_till_done()
    assert hass.states.get(number.entity_id).state != "unavailable"


async def test_removal_drops_the_listener(hass):
    _, number, ids = _setup(hass)
    await _add(hass, number)
    assert number._unsub_art_sources is not None
    await number.async_remove()
    assert number._unsub_art_sources is None
