"""IP Control expert settings exposed from the verified method maps.

notes/QN55LS03FAFXZA/IPCONTROL_DECOMPILED.md lists, per method, what a
QN55LS03FAFXZA accepted live, and notes/TQ65S95HFTXXC/IPCONTROL_LIVE.md the same
for a TQ65S95HFTXXC, and notes/QE65S99HATXXU/IPCONTROL_LIVE.md for a
QE65S99HATXXU. The entities must only use methods and values marked
accepted in one of them, and every picture setting must be a picture write so a
-32002 ("not in this picture mode / input") is reported as a TV-side state
rather than a broken pairing.

ip_settings.py and ipcontrol.py need Home Assistant to import, so their pieces
are exec'd or parsed from source.
"""

import asyncio
import ast
from pathlib import Path
import textwrap
import unittest

ROOT = Path(__file__).parents[1]
CC = ROOT / "custom_components" / "samsungtv_smart"
NOTES = "\n".join(
    (ROOT / "notes" / model / name).read_text()
    for model, name in (
        ("QN55LS03FAFXZA", "IPCONTROL_DECOMPILED.md"),
        ("TQ65S95HFTXXC", "IPCONTROL_LIVE.md"),
        ("QE65S99HATXXU", "IPCONTROL_LIVE.md"),
    )
)
IPCONTROL = (CC / "api" / "ipcontrol.py").read_text()
SETTINGS = (CC / "ip_settings.py").read_text()


def _settings():
    """Collect IPSetting(...) calls: (key, method, param, options)."""
    tree = ast.parse(SETTINGS)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "IPSetting":
            args = [a.value for a in node.args[:3]]
            options = None
            for kw in node.keywords:
                if kw.arg == "options":
                    options = ast.literal_eval(kw.value)
            found.append((*args, options))
    return found


SETTINGS_FOUND = _settings()


def _verified_row(method):
    """The accepted rows for the method, joined: a value verified on either TV counts."""
    rows = [
        line
        for line in NOTES.splitlines()
        if line.startswith(f"| `{method}` |") and "✅" in line
    ]
    return " ".join(rows) if rows else None


class TableTest(unittest.TestCase):
    def test_eleven_settings(self):
        self.assertEqual(len(SETTINGS_FOUND), 11)

    def test_every_method_was_accepted_live(self):
        for key, method, _param, _options in SETTINGS_FOUND:
            with self.subTest(key=key):
                self.assertIsNotNone(_verified_row(method), method)

    def test_select_values_are_ones_the_tv_accepted(self):
        for key, method, _param, options in SETTINGS_FOUND:
            if not options:
                continue
            row = _verified_row(method)
            for tv_value in options.values():
                with self.subTest(key=key, value=tv_value):
                    self.assertIn(f"`{tv_value}`", row)

    def test_option_keys_are_valid_translation_keys(self):
        for key, _m, _p, options in SETTINGS_FOUND:
            for option in options or {}:
                self.assertRegex(option, r"^[a-z0-9_]+$", key)

    def test_all_are_picture_writes(self):
        block = IPCONTROL[IPCONTROL.index("PICTURE_WRITE_METHODS = frozenset(") :]
        block = block[: block.index(")")]
        for key, method, _param, _options in SETTINGS_FOUND:
            if method in ("autoPowerSavingControl", "energySavingSolutionControl"):
                continue  # eco, not picture: a -32002 there is a plain error
            self.assertIn(f'"{method}"', block, key)

    def test_translated_in_en_and_bg(self):
        import json

        for lang in ("en", "bg"):
            data = json.loads((CC / "translations" / f"{lang}.json").read_text())
            names = {
                key
                for domain in data["entity"].values()
                for key in domain
            }
            for key, *_ in SETTINGS_FOUND:
                self.assertIn(key, names, f"{lang}: {key}")


def _load_client_methods():
    start = IPCONTROL.index("    async def async_get_setting(")
    end = IPCONTROL.index("    async def async_get_color_tone(")
    source = textwrap.dedent(IPCONTROL[start:end])

    class SamsungIPControlError(Exception):
        pass

    namespace = {"Any": object, "SamsungIPControlError": SamsungIPControlError}
    exec(compile(source, "ipcontrol.py", "exec"), namespace)
    return namespace, SamsungIPControlError


NS, ERR = _load_client_methods()


class _Client:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    async def _async_request(self, method, params=None):
        self.calls.append((method, params))
        return self.answer


class ClientTest(unittest.TestCase):
    def test_get_reads_the_param(self):
        client = _Client({"autoMotionPlus": "Auto"})
        value = asyncio.run(NS["async_get_setting"](client, "autoMotionPlusControl", "autoMotionPlus"))
        self.assertEqual(value, "Auto")
        self.assertEqual(client.calls, [("autoMotionPlusControl", None)])

    def test_get_without_the_param_is_an_error(self):
        client = _Client({"other": 1})
        with self.assertRaises(ERR):
            asyncio.run(NS["async_get_setting"](client, "motionLightingControl", "motionLighting"))

    def test_set_sends_the_param_and_returns_the_echo(self):
        client = _Client({"gamma.BT1886": 2})
        echoed = asyncio.run(
            NS["async_set_setting"](client, "gamma.BT1886Control", "gamma.BT1886", 2)
        )
        self.assertEqual(echoed, 2)
        self.assertEqual(client.calls, [("gamma.BT1886Control", {"gamma.BT1886": 2})])

    def test_set_without_echo_returns_what_was_sent(self):
        client = _Client({})
        echoed = asyncio.run(
            NS["async_set_setting"](client, "motionLightingControl", "motionLighting", "On")
        )
        self.assertEqual(echoed, "On")


if __name__ == "__main__":
    unittest.main()
