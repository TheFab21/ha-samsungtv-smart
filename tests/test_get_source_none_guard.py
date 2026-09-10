"""_get_source() must never do "ST_" + None (ollo69/ha-samsungtv-smart#416).

SmartThings reports STATE_ON a moment before it resolves the active input, so
self._st.source is None during that window. Upstream v0.14.5 crashes there with

    cloud_key = "ST_" + self._st.source
    TypeError: can only concatenate str (not "NoneType") to str

Our _get_source() guards it: the concatenation sits behind `elif self._st.source:`
with an `else: cloud_key = None`. This pins that guard so a future rewrite of the
branch cannot reintroduce the crash — media_player.py can't be imported without
Home Assistant, so the exact source of the three-way branch is extracted and
executed against a None source.
"""

from pathlib import Path
import re
import textwrap
import unittest

MEDIA_PLAYER = (
    Path(__file__).parents[1]
    / "custom_components"
    / "samsungtv_smart"
    / "media_player.py"
).read_text()


def _cloud_key_branch() -> str:
    """The exact source→cloud_key branch from _get_source(), as a callable body.

    Extracted from media_player.py (not reimplemented) so this test fails if the
    branch is rewritten in a way that changes its shape — which is the point.
    """
    body = MEDIA_PLAYER[MEDIA_PLAYER.index("        if self._st.source in [") :]
    body = body[: body.index("cloud_key = None") + len("cloud_key = None")]
    body = textwrap.dedent(body)
    # Drive it with a plain `source` instead of the live SmartThings client.
    return body.replace("self._st.source", "source")


BRANCH = _cloud_key_branch()


def _cloud_key_for(source):
    namespace = {"source": source}
    exec(compile(BRANCH, "cloud_key_branch", "exec"), namespace)
    return namespace["cloud_key"]


class CloudKeyBranchTest(unittest.TestCase):
    """The branch that maps a SmartThings source to an ST_ key."""

    def test_a_none_source_does_not_crash_and_yields_no_key(self):
        # The exact upstream crash: "ST_" + None. Must be None, not raise.
        self.assertIsNone(_cloud_key_for(None))

    def test_an_empty_source_yields_no_key(self):
        self.assertIsNone(_cloud_key_for(""))

    def test_the_tuner_aliases_map_to_st_tv(self):
        for source in ("TV", "digitalTv", "dtv"):
            self.assertEqual(_cloud_key_for(source), "ST_TV", source)

    def test_a_real_input_is_prefixed(self):
        self.assertEqual(_cloud_key_for("HDMI1"), "ST_HDMI1")
        self.assertEqual(_cloud_key_for("HDMI2"), "ST_HDMI2")


class GuardShapeTest(unittest.TestCase):
    """The concatenation must stay behind a truthiness guard."""

    def test_the_concatenation_is_guarded(self):
        # An unconditional `cloud_key = "ST_" + self._st.source` (no `elif
        # self._st.source:` in front of it) would be the upstream bug.
        self.assertIn("elif self._st.source:\n", MEDIA_PLAYER)
        self.assertIn('cloud_key = "ST_" + self._st.source', MEDIA_PLAYER)
        # And exactly one such concatenation on a source value exists.
        self.assertEqual(
            len(re.findall(r'"ST_" \+ self\._st\.source', MEDIA_PLAYER)), 1
        )


if __name__ == "__main__":
    unittest.main()
