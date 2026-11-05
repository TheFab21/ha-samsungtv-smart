"""The media player's sources as a select.

Reported on a Frame 2024: media_player.samsung_hacs published
``source_list: TV, CINEMA 60``, yet the Home Assistant mobile app's media
player dialog offered TV alone. The select mirrors the published list.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.samsungtv_smart import select as sel


def _state(state="on", source_list=("TV", "CINEMA 60"), source="TV"):
    return SimpleNamespace(
        state=state,
        attributes={"source_list": list(source_list), "source": source},
    )


def _entity():
    entity = object.__new__(sel.SamsungTVSourceSelect)
    entity._attr_options = []
    entity._attr_current_option = None
    entity._attr_available = False
    entity._device_name = "The Frame Salon"
    entity._mp_entity_id = "media_player.samsung_hacs"
    return entity


def test_every_published_source_is_an_option():
    entity = _entity()
    assert entity._apply(_state()) is True
    assert entity.options == ["TV", "CINEMA 60"]
    assert entity.current_option == "TV"
    assert entity._attr_available is True
    assert entity._apply(_state()) is False


def test_a_source_outside_the_list_is_shown_not_unknown():
    # A select whose current_option is not in its options reads "Unknown".
    # The live source (a channel, an app the home screen doesn't list) is
    # added to the options so the entity reflects the TV instead of blanking.
    entity = _entity()
    assert entity._apply(_state(source="Netflix")) is True
    assert entity.current_option == "Netflix"
    assert "Netflix" in entity.options
    assert entity._attr_available is True


def test_off_keeps_the_list_but_is_unavailable():
    entity = _entity()
    entity._apply(_state())
    entity._apply(_state(state="off", source_list=(), source=None))
    assert entity.options == ["TV", "CINEMA 60"]
    assert entity._attr_available is False
    assert entity.current_option is None


def test_selecting_goes_through_media_player_select_source():
    entity = _entity()
    call = AsyncMock()
    entity.hass = SimpleNamespace(services=SimpleNamespace(async_call=call))
    asyncio.run(entity.async_select_option("CINEMA 60"))
    call.assert_awaited_once_with(
        "media_player",
        "select_source",
        {"entity_id": "media_player.samsung_hacs", "source": "CINEMA 60"},
        blocking=True,
    )
