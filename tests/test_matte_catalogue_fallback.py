"""Matte selects fall back to the built-in catalogue when the TV won't enumerate.

A 2019 Frame (art API 0.97, #315) never answers get_matte_list, so the Matte
Type / Matte Color selects used to stay stuck on a single option ("none" /
"polar"). When the enumeration keeps failing, seed the selects from the known
Samsung matte catalogue so they are usable; a TV that does answer is unaffected.
"""

import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

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

from custom_components.samsungtv_smart import select as select_mod  # noqa: E402


async def _run_load(art_api):
    type_select = MagicMock()
    color_select = MagicMock()
    type_select.async_refresh_current = AsyncMock()
    color_select.async_refresh_current = AsyncMock()

    # Don't actually wait 30 s between the (shortened) retries.
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.title = "Frame"

    with (
        patch.object(select_mod, "_MAX_RETRIES", 2),
        patch.object(select_mod, "_frame_art_api_active", return_value=True),
        patch.object(select_mod.asyncio, "sleep", AsyncMock()),
    ):
        await select_mod._load_matte_options(
            MagicMock(), entry, art_api, type_select, color_select
        )
    return type_select, color_select


async def test_fallback_catalogue_when_get_matte_list_times_out():
    art_api = MagicMock()
    art_api.get_matte_list = AsyncMock(side_effect=TimeoutError())

    type_select, color_select = await _run_load(art_api)

    type_select.set_options.assert_called_once_with(
        select_mod._MATTE_TYPES_FALLBACK
    )
    color_select.set_options.assert_called_once_with(
        select_mod._MATTE_COLORS_FALLBACK
    )
    # The real catalogue must have been read back against the seeded options.
    type_select.async_refresh_current.assert_awaited()
    color_select.async_refresh_current.assert_awaited()
    # Sanity: the fallback is a real catalogue, not the stuck single option.
    assert "shadowbox" in select_mod._MATTE_TYPES_FALLBACK
    assert len(select_mod._MATTE_COLORS_FALLBACK) > 1


async def test_real_list_is_used_and_fallback_not_applied():
    art_api = MagicMock()
    art_api.get_matte_list = AsyncMock(
        return_value=(["none", "shadowbox"], ["black", "polar"])
    )

    type_select, color_select = await _run_load(art_api)

    # The TV answered, so the selects take its lists and the fallback is never
    # substituted.
    type_select.set_options.assert_called_once_with(["none", "shadowbox"])
    color_select.set_options.assert_called_once_with(["black", "polar"])
    for call in type_select.set_options.call_args_list:
        assert call.args[0] != select_mod._MATTE_TYPES_FALLBACK
