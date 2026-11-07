"""Reconfigure must be able to drop SmartThings entirely (#258).

A user who set the integration up with SmartThings (PAT/ST/OAuth) had no way
to remove it: a blank PAT on reconfigure fell back to the stored key, and the
OAuth token was never cleared — so an entry whose OAuth application credentials
had been deleted kept trying to refresh and spammed "expired credentials".

reconfigure_auth now offers "No SmartThings (local only)", which calls
_apply_local_only_and_reload to strip every credential. config_flow.py cannot
be imported without Home Assistant, so the handler wiring is checked
structurally and the strip is reproduced against the real key set. const.py
imports cleanly, so AUTH_METHOD_NONE is checked directly.
"""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
CONFIG_FLOW = (ROOT / "config_flow.py").read_text()


def _load_const():
    spec = importlib.util.spec_from_file_location(
        "samsungtv_const_local_only", ROOT / "const.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


const = _load_const()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class AuthMethodNoneTest(unittest.TestCase):
    """The new auth method exists and is offered on reconfigure."""

    def test_the_constant_is_defined(self):
        self.assertEqual(const.AUTH_METHOD_NONE, "none")

    def test_the_form_offers_local_only(self):
        self.assertIn(
            'auth_options[AUTH_METHOD_NONE] = "🚫 No SmartThings (local only)"',
            CONFIG_FLOW,
        )

    def test_selecting_it_short_circuits_before_reading_a_key(self):
        # In reconfigure_auth the NONE branch must come before the api_key /
        # OAuth handling, so a stray typed key can't be picked up instead.
        handler = _block(
            CONFIG_FLOW,
            "        method = user_input.get(CONF_AUTH_METHOD_SELECT",
            "        api_key = user_input.get(CONF_API_KEY)",
        )
        none_branch = handler.index("if method == AUTH_METHOD_NONE:")
        oauth_branch = handler.index("if method == AUTH_METHOD_OAUTH:")
        self.assertIn(
            "return self._apply_local_only_and_reload()",
            handler[none_branch:oauth_branch],
        )
        self.assertLess(none_branch, oauth_branch)


class StripBehaviourTest(unittest.TestCase):
    """_apply_local_only_and_reload removes exactly the credential keys."""

    def setUp(self):
        self.block = _block(
            CONFIG_FLOW,
            "    def _apply_local_only_and_reload",
            "\n    @callback",
        )

    def test_it_strips_every_credential_including_the_oauth_token(self):
        stripped = _block(self.block, "stripped = {", "}")
        for key in (
            "CONF_API_KEY",
            "CONF_OAUTH_TOKEN",
            "CONF_DEVICE_ID",
            "CONF_ST_ENTRY_UNIQUE_ID",
            "CONF_ST_PICTURE_MODE_CAPABILITY",
            '"auth_implementation"',
        ):
            self.assertIn(key, stripped, key)

    def test_it_replaces_data_rather_than_merging(self):
        # data_updates can't delete keys; a full data= replace can.
        self.assertIn("self.async_update_and_abort(", self.block)
        self.assertIn("entry, data=data,", self.block)
        self.assertNotIn("data_updates=", self.block)

    def test_it_marks_the_entry_as_local_only_and_forces_a_reload(self):
        self.assertIn("data[CONF_AUTH_METHOD] = AUTH_METHOD_NONE", self.block)
        self.assertIn(
            "data[CONF_RECONFIGURE_GENERATION] = uuid.uuid4().hex", self.block
        )

    def test_the_reproduced_strip_keeps_the_connection_and_drops_the_cloud(self):
        # Reproduce the comprehension against a realistic entry.data.
        stripped = {
            "api_key",
            "oauth_token",
            "device_id",
            "st_entry_unique_id",
            "st_picture_mode_capability",
            "auth_implementation",
        }
        entry_data = {
            "host": "1.2.3.4",
            "port": 8002,
            "token": "ws-token",
            "name": "Living Room",
            "api_key": "secret",
            "oauth_token": {"refresh_token": "r", "access_token": "a"},
            "device_id": "dev-1",
            "st_entry_unique_id": "st-1",
            "st_picture_mode_capability": "cap",
            "auth_implementation": "samsungtv_smart",
        }
        result = {k: v for k, v in entry_data.items() if k not in stripped}
        # The cloud is gone...
        for gone in stripped:
            self.assertNotIn(gone, result)
        # ...the local connection is untouched.
        self.assertEqual(result["host"], "1.2.3.4")
        self.assertEqual(result["token"], "ws-token")


if __name__ == "__main__":
    unittest.main()
