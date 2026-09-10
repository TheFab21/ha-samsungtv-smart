"""Runtime-learned entry.data keys must not reload the integration (#12).

_update_listener reloads the entry whenever _reload_fingerprint changes.
Facts the live clients learn and refresh at runtime (WS/OAuth tokens, the
Art/REST ports on 2020 Frames, capability flags) are persisted only to
survive a restart — reloading on them made the entry flap
unavailable -> unknown -> restored every few minutes on an unstable
connection. They now sit in _NO_RELOAD_DATA_KEYS. A reconfigure still
reloads: it bumps CONF_RECONFIGURE_GENERATION, which is deliberately not
excluded.

const.py imports cleanly without Home Assistant; __init__.py and
config_flow.py do not, so those are checked structurally and the fingerprint
behaviour is reproduced against the real exclusion set.
"""

import importlib.util
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
INIT = (ROOT / "__init__.py").read_text()
CONFIG_FLOW = (ROOT / "config_flow.py").read_text()


def _load_const():
    spec = importlib.util.spec_from_file_location(
        "samsungtv_const_under_test", ROOT / "const.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


const = _load_const()


def _excluded_key_names() -> set[str]:
    """The CONF_* names listed inside _NO_RELOAD_DATA_KEYS in __init__.py."""
    block = INIT[INIT.index("_NO_RELOAD_DATA_KEYS = (") :]
    # Slice to the closing paren at the start of a line — the block contains
    # comments with parens ("(#12)"), so block.index(")") would truncate.
    block = block[: block.index("\n)")]
    # Only real tuple entries ("    CONF_X,"), never CONF_* names that appear
    # inside the explanatory comments (which mention deliberately-excluded and
    # deliberately-kept keys).
    names = set()
    for line in block.splitlines():
        m = re.match(r"\s+(CONF_[A-Z_]+),\s*$", line)
        if m:
            names.add(m.group(1))
    return names


# CONF_TOKEN / CONF_PORT (and the identity keys used below) come from
# homeassistant.const, not our const.py; their string values are stable HA
# public constants.
_HA_CONST = {
    "CONF_TOKEN": "token",
    "CONF_PORT": "port",
    "CONF_HOST": "host",
    "CONF_DEVICE_ID": "device_id",
    "CONF_API_KEY": "api_key",
}


def _resolve(name: str) -> str:
    return getattr(const, name, None) or _HA_CONST[name]


def _excluded_values() -> set[str]:
    """The excluded key NAMES resolved to their string values."""
    return {_resolve(name) for name in _excluded_key_names()}


class ExclusionSetTest(unittest.TestCase):
    """The learned keys are excluded; identity/connection settings are not."""

    def test_the_runtime_learned_keys_are_all_excluded(self):
        excluded = _excluded_values()
        for name in (
            "CONF_TOKEN",
            "CONF_OAUTH_TOKEN",
            "CONF_PORT",
            "CONF_REST_PORT",
            "CONF_SUPPORTS_GET_BRIGHTNESS",
            "CONF_SUPPORTS_GET_COLOR_TEMPERATURE",
            "CONF_IS_FRAME_TV",
            "CONF_SLIDESHOW_API",
            "CONF_IP_CONTROL_TOKEN",
            "CONF_IP_CONTROL_MODEL_ID",
            "CONF_IP_CONTROL_FW_VERSION",
            "CONF_ST_PICTURE_MODE_CAPABILITY",
        ):
            self.assertIn(_resolve(name), excluded, name)

    def test_identity_and_connection_settings_still_reload(self):
        excluded = _excluded_values()
        # Changing these must still tear down and rebuild the clients.
        for value in ("host", "device_id", "api_key"):
            self.assertNotIn(value, excluded, value)

    def test_the_reconfigure_nonce_is_not_excluded(self):
        # It is the key that guarantees a reconfigure reloads.
        self.assertNotIn(const.CONF_RECONFIGURE_GENERATION, _excluded_values())


class FingerprintBehaviourTest(unittest.TestCase):
    """Reproduce _reload_fingerprint against the real exclusion set."""

    def setUp(self):
        self.excluded = _excluded_values()

    def _fingerprint(self, data):
        # Mirrors _reload_fingerprint's data half (options omitted here).
        return {k: v for k, v in data.items() if k not in self.excluded}

    def test_a_rotated_token_does_not_change_the_fingerprint(self):
        before = {"host": "1.2.3.4", _resolve("CONF_TOKEN"): "old"}
        after = {"host": "1.2.3.4", _resolve("CONF_TOKEN"): "new"}
        self.assertEqual(self._fingerprint(before), self._fingerprint(after))

    def test_a_relearned_rest_port_does_not_change_the_fingerprint(self):
        before = {"host": "1.2.3.4", const.CONF_REST_PORT: 8001}
        after = {"host": "1.2.3.4", const.CONF_REST_PORT: 8002}
        self.assertEqual(self._fingerprint(before), self._fingerprint(after))

    def test_an_oauth_refresh_does_not_change_the_fingerprint(self):
        before = {const.CONF_OAUTH_TOKEN: {"access_token": "a"}}
        after = {const.CONF_OAUTH_TOKEN: {"access_token": "b"}}
        self.assertEqual(self._fingerprint(before), self._fingerprint(after))

    def test_a_host_change_does_change_the_fingerprint(self):
        before = {"host": "1.2.3.4", _resolve("CONF_TOKEN"): "t"}
        after = {"host": "5.6.7.8", _resolve("CONF_TOKEN"): "t"}
        self.assertNotEqual(self._fingerprint(before), self._fingerprint(after))

    def test_a_reconfigure_nonce_bump_does_change_the_fingerprint(self):
        before = {_resolve("CONF_TOKEN"): "t", const.CONF_RECONFIGURE_GENERATION: "aaa"}
        after = {_resolve("CONF_TOKEN"): "t2", const.CONF_RECONFIGURE_GENERATION: "bbb"}
        # Token rotated (ignored) but the nonce moved -> reload.
        self.assertNotEqual(self._fingerprint(before), self._fingerprint(after))


class ReconfigureBumpsNonceTest(unittest.TestCase):
    """Every reconfigure save path must set the nonce, or it won't reload."""

    def test_all_three_reconfigure_saves_bump_the_generation(self):
        # main reconfigure, ST device re-pick, IP Control pairing.
        self.assertEqual(
            CONFIG_FLOW.count("CONF_RECONFIGURE_GENERATION: uuid.uuid4().hex")
            + CONFIG_FLOW.count(
                "updates[CONF_RECONFIGURE_GENERATION] = uuid.uuid4().hex"
            ),
            3,
        )

    def test_uuid_is_imported(self):
        self.assertIn("\nimport uuid\n", CONFIG_FLOW)


if __name__ == "__main__":
    unittest.main()
