"""Every {placeholder} in a dialog string must be supplied (#302).

Two spots showed "formatjs Error: MISSING_VALUE" and broken links:

- the "Add application credential" dialog: its description uses
  {smartthings_portal}, {oauth_docs} and {callback_url}, but the integration
  never implemented async_get_description_placeholders, so none were provided;
- the "Successfully configured {name}" message at the end of config: the flow
  created the entry without a "name" placeholder.
"""

import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"

if "pysmartthings" not in sys.modules:
    pysmartthings = ModuleType("pysmartthings")

    class _Names:
        def __getattr__(self, name):
            return name

    pysmartthings.Attribute = _Names()
    pysmartthings.Capability = _Names()
    pysmartthings.Command = _Names()
    pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = pysmartthings

from custom_components.samsungtv_smart import (  # noqa: E402
    application_credentials as ac,
)
from custom_components.samsungtv_smart.config_flow import (  # noqa: E402
    SamsungTVSmartOAuth2FlowHandler,
)

STRINGS = json.loads((ROOT / "strings.json").read_text())


def _placeholders(text: str) -> set[str]:
    """The {placeholder} names a formatjs string interpolates."""
    return set(re.findall(r"(?<!\{)\{([a-z_][a-z0-9_]*)\}(?!\})", text))


# ── Add application credential dialog ────────────────────────────────────────


CONFIG = STRINGS["config"]


async def test_the_dialog_supplies_every_placeholder_its_description_uses():
    description = STRINGS["application_credentials"]["description"]
    referenced = _placeholders(description)
    # The description really does reference all three (guard the test itself).
    assert referenced == {"smartthings_portal", "oauth_docs", "callback_url"}

    hass = SimpleNamespace(config=SimpleNamespace(components={"my"}))
    provided = await ac.async_get_description_placeholders(hass)

    assert referenced <= provided.keys()
    assert all(provided[key].startswith("http") for key in referenced)


async def test_callback_url_is_the_flow_redirect_uri():
    hass = SimpleNamespace(config=SimpleNamespace(components={"my"}))
    provided = await ac.async_get_description_placeholders(hass)
    # "my" enabled: the My Home Assistant OAuth redirect, as the flow uses.
    assert provided["callback_url"] == "https://my.home-assistant.io/redirect/oauth"


async def test_callback_url_falls_back_when_the_redirect_uri_cannot_be_read():
    hass = SimpleNamespace(config=SimpleNamespace(components=set()))
    with patch.object(
        ac.config_entry_oauth2_flow,
        "async_get_redirect_uri",
        side_effect=RuntimeError("no request in context"),
    ):
        provided = await ac.async_get_description_placeholders(hass)
    assert provided["callback_url"] == ac._MY_REDIRECT_URL


# ── Successfully configured {name} ───────────────────────────────────────────


def _config_flow(name="Living Room TV"):
    flow = object.__new__(SamsungTVSmartOAuth2FlowHandler)
    flow.context = {"source": "user"}
    flow.flow_id = "test-flow"
    flow.handler = "samsungtv_smart"
    flow._host = "192.168.1.10"
    flow._name = name
    flow._ws_name = name
    flow._tv_info = SimpleNamespace(ws_port=8002)
    flow._auth_method = None
    flow._token = None
    flow._oauth_data = None
    flow._api_key = None
    flow._device_id = None
    flow._st_entry_unique_id = None
    flow._device_info = {}
    flow._ping_port = None
    return flow


def test_create_entry_supplies_the_name_placeholder():
    name = "Living Room TV"
    result = _config_flow(name)._save_entry()

    # The success message needs exactly this placeholder.
    assert _placeholders(CONFIG["create_entry"]["default"]) == {"name"}
    assert result["description_placeholders"] == {"name": name}
    assert result["title"] == name


# ── Entity names ─────────────────────────────────────────────────────────────

# The platform an _attr_translation_key belongs to, by the file it lives in.
_ENTITY_PLATFORM = {
    "button.py": "button",
    "switch.py": "switch",
    "sensor.py": "sensor",
    "number.py": "number",
    "binary_sensor.py": "binary_sensor",
}


def _entity_translation_keys() -> set[tuple[str, str]]:
    """(platform, translation_key) for every entity that declares one.

    An entity with _attr_has_entity_name and _attr_translation_key but no
    entity.<platform>.<key>.name ends up unnamed unless its device class names
    it: switch.power and sensor.brightness_intensity did (#302).
    """
    found = set()
    for filename, platform in _ENTITY_PLATFORM.items():
        path = ROOT / filename
        if not path.exists():
            continue
        for key in re.findall(
            r'_attr_translation_key\s*=\s*["\']([a-z_0-9]+)["\']', path.read_text()
        ):
            found.add((platform, key))
    return found


def test_every_entity_translation_key_has_a_name():
    entity = STRINGS["entity"]
    missing = [
        f"entity.{platform}.{key}.name"
        for platform, key in sorted(_entity_translation_keys())
        if key not in entity.get(platform, {}) or "name" not in entity[platform][key]
    ]
    assert not missing, f"missing entity names in strings.json: {missing}"


def test_strings_and_en_entity_sections_match():
    en = json.loads((ROOT / "translations" / "en.json").read_text())
    assert STRINGS["entity"] == en["entity"]
