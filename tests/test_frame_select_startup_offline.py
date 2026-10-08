"""Regression tests for Frame matte discovery when HA starts with the TV off."""

import inspect
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


class _Names:
    def __getattr__(self, name):
        return name


pysmartthings = sys.modules.setdefault("pysmartthings", ModuleType("pysmartthings"))
for _name in ("Attribute", "Capability", "Command"):
    if not hasattr(pysmartthings, _name):
        setattr(pysmartthings, _name, _Names())
if not hasattr(pysmartthings, "SmartThings"):
    pysmartthings.SmartThings = object

from custom_components.samsungtv_smart import select as select_module  # noqa: E402


@pytest.mark.parametrize(
    ("state_value", "art_mode_status", "expected"),
    [
        ("on", "off", True),
        # 2021 Frame observed on-device: Art Mode can remain media_player=on.
        ("on", "on", True),
        # 2025 Frame: Art Mode may be published as media_player=off.
        ("off", "on", True),
        ("off", "off", False),
        ("off", None, False),
        ("unavailable", "on", False),
        ("unknown", "on", False),
    ],
)
def test_frame_art_api_active_follows_published_panel_state(
    monkeypatch, state_value, art_mode_status, expected
):
    """Art Mode outranks OFF; unknown/unavailable are not proof of reachability."""
    entity = SimpleNamespace(domain="media_player", entity_id="media_player.frame")
    registry = SimpleNamespace(
        entities=SimpleNamespace(
            get_entries_for_config_entry_id=lambda _entry_id: [entity]
        )
    )
    monkeypatch.setattr(select_module.er, "async_get", lambda _hass: registry)

    attrs = {}
    if art_mode_status is not None:
        attrs["art_mode_status"] = art_mode_status

    state = SimpleNamespace(state=state_value, attributes=attrs)
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: state))

    assert select_module._frame_art_api_active(hass, "entry") is expected


def test_frame_art_api_active_ignores_stale_duplicate(monkeypatch):
    """An unavailable duplicate must not veto another live media_player."""
    entities = [
        SimpleNamespace(
            domain="media_player",
            entity_id="media_player.frame_old",
        ),
        SimpleNamespace(
            domain="media_player",
            entity_id="media_player.frame",
        ),
    ]
    registry = SimpleNamespace(
        entities=SimpleNamespace(
            get_entries_for_config_entry_id=lambda _entry_id: entities
        )
    )
    monkeypatch.setattr(select_module.er, "async_get", lambda _hass: registry)

    states = {
        "media_player.frame_old": SimpleNamespace(
            state="unavailable",
            attributes={"art_mode_status": "on"},
        ),
        "media_player.frame": SimpleNamespace(
            state="off",
            attributes={"art_mode_status": "on"},
        ),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda entity_id: states.get(entity_id))
    )

    assert select_module._frame_art_api_active(hass, "entry") is True


def test_frame_art_api_active_waits_until_media_player_exists(monkeypatch):
    """Platform startup can race media_player registration; defer until it exists."""
    registry = SimpleNamespace(
        entities=SimpleNamespace(get_entries_for_config_entry_id=lambda _entry_id: [])
    )
    monkeypatch.setattr(select_module.er, "async_get", lambda _hass: registry)
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _entity_id: None))

    assert select_module._frame_art_api_active(hass, "entry") is False


@pytest.mark.asyncio
async def test_matte_loader_defers_without_spending_tv_attempts(monkeypatch):
    """Sleeping checks followed by wake produce only one real TV request."""
    local_state = iter((False, False, True))
    monkeypatch.setattr(
        select_module,
        "_frame_art_api_active",
        lambda _hass, _entry_id: next(local_state),
    )

    sleep = AsyncMock()
    monkeypatch.setattr(select_module.asyncio, "sleep", sleep)

    art_api = SimpleNamespace(
        get_matte_list=AsyncMock(
            return_value=(
                [{"matte_type": "none"}, {"matte_type": "modern"}],
                [{"color": "black"}, {"color": "polar"}],
            )
        )
    )
    type_select = SimpleNamespace(
        set_options=Mock(),
        async_refresh_current=AsyncMock(),
    )
    color_select = SimpleNamespace(
        set_options=Mock(),
        async_refresh_current=AsyncMock(),
    )
    entry = SimpleNamespace(entry_id="entry", title="Frame")

    await select_module._load_matte_options(
        SimpleNamespace(),
        entry,
        art_api,
        type_select,
        color_select,
    )

    assert sleep.await_count == 2
    art_api.get_matte_list.assert_awaited_once_with(include_color=True)
    type_select.set_options.assert_called_once_with(["none", "modern"])
    color_select.set_options.assert_called_once_with(["black", "polar"])
    type_select.async_refresh_current.assert_awaited_once()
    color_select.async_refresh_current.assert_awaited_once()


def test_matte_entities_do_not_read_tv_from_async_added_to_hass():
    """Adding matte entities must not perform two serial 5 s Art API reads."""
    assert "async_added_to_hass" not in select_module.SamsungTVMatteTypeSelect.__dict__
    assert "async_added_to_hass" not in select_module.SamsungTVMatteColorSelect.__dict__


def test_long_lived_matte_loader_is_owned_by_config_entry():
    """An offline wait must be cancelled automatically when the entry unloads."""
    source = inspect.getsource(select_module.async_setup_entry)

    matte_call = source.index("_load_matte_options(")
    entry_task = source.rfind("entry.async_create_background_task(", 0, matte_call)
    hass_task = source.rfind("hass.async_create_background_task(", 0, matte_call)

    assert entry_task != -1
    assert entry_task > hass_task
