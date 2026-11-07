"""The Art Mode ready-wait says what it checked (#319, #408).

``_wait_for_tv_ready`` calls ``supported()``, the art support check: a REST GET
on port 8001 for a Frame, answered in standby too, and no request at all for a
set the art-app probe already confirmed. Its log line said "the art channel
answers", which was never asked, and it pointed a #319 diagnosis at the art
channel while the log showed a half-awake TV answering REST as standby.
"""

from pathlib import Path

SWITCH = (
    Path(__file__).parents[1] / "custom_components" / "samsungtv_smart" / "switch.py"
).read_text()
WAIT = SWITCH[SWITCH.index("    async def _wait_for_tv_ready") :]
WAIT = WAIT[: WAIT.index("\n    async def ", 1)]


def test_the_wait_no_longer_claims_the_art_channel_answered():
    assert "answers, which a TV in standby also does" not in WAIT
    assert "answering on the art channel" not in WAIT
    assert "Try to connect to Art API" not in WAIT


def test_the_wait_names_the_check_it_ran():
    assert "the Frame support " in WAIT
    assert "check passes, as it does in standby" in WAIT
    # The gate itself is unchanged: support check, then the power state.
    assert "await self._art_api.supported()" in WAIT
    assert "await self._is_tv_on()" in WAIT
