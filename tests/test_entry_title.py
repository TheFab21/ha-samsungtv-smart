"""The entry title follows a reconfigure of the auth method (#409).

The config flow titles a new entry "<name> (OAuth)", "<name> (SmartThings)" or
the bare name. A reconfigure that changed the auth method left the suffix
behind: an entry switched from OAuth to local only kept reading "(OAuth)",
which misled the reading of its own diagnostics. A title the user chose must
survive, so only a still-generated title is rewritten.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
CONFIG_FLOW = (ROOT / "config_flow.py").read_text()

_spec = importlib.util.spec_from_file_location(
    "samsungtv_const_entry_title", ROOT / "const.py"
)
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)

NAME = "stue_tv"


def test_a_new_entry_is_titled_by_its_auth_method():
    assert const.auto_entry_title(NAME, const.AUTH_METHOD_OAUTH) == "stue_tv (OAuth)"
    assert (
        const.auto_entry_title(NAME, const.AUTH_METHOD_PAT) == "stue_tv (SmartThings)"
    )
    assert (
        const.auto_entry_title(NAME, const.AUTH_METHOD_ST_ENTRY)
        == "stue_tv (SmartThings)"
    )
    assert const.auto_entry_title(NAME, const.AUTH_METHOD_NONE) == "stue_tv"
    assert const.auto_entry_title(NAME, None) == "stue_tv"


def test_oauth_to_local_only_drops_the_suffix():
    # The #409 entry: "stue_tv (OAuth)" with auth_method "none".
    assert (
        const.retitled_entry("stue_tv (OAuth)", NAME, const.AUTH_METHOD_NONE)
        == "stue_tv"
    )


def test_every_generated_title_follows_every_method():
    titles = ["stue_tv", "stue_tv (OAuth)", "stue_tv (SmartThings)"]
    for current in titles:
        for method in (
            const.AUTH_METHOD_OAUTH,
            const.AUTH_METHOD_PAT,
            const.AUTH_METHOD_ST_ENTRY,
            const.AUTH_METHOD_NONE,
        ):
            expected = const.auto_entry_title(NAME, method)
            got = const.retitled_entry(current, NAME, method)
            assert got == (None if expected == current else expected), (current, method)


def test_a_title_the_user_chose_is_left_alone():
    for current in ("Salon", "stue_tv (Living room)", "STUE_TV", "stue_tv (oauth)"):
        assert const.retitled_entry(current, NAME, const.AUTH_METHOD_NONE) is None


def test_nothing_to_do_returns_none():
    # A plain token reauth of an OAuth entry must not touch the entry.
    assert (
        const.retitled_entry("stue_tv (OAuth)", NAME, const.AUTH_METHOD_OAUTH) is None
    )
    # No stored name: nothing to build a title from.
    assert const.retitled_entry("stue_tv (OAuth)", None, const.AUTH_METHOD_NONE) is None


def _block(start: str) -> str:
    begin = CONFIG_FLOW.index(start)
    end = CONFIG_FLOW.index("\n    def ", begin + 1)
    return CONFIG_FLOW[begin:end]


def test_each_auth_change_path_retitles():
    # config_flow.py cannot be imported against the pinned test HA, so the
    # wiring is checked in the source, like test_local_only_auth.py.
    local = _block("    def _apply_local_only_and_reload")
    assert "self._retitle(entry, AUTH_METHOD_NONE)" in local
    st = _block("    def _apply_reconfigure_and_reload")
    assert "self._retitle(entry, updates[CONF_AUTH_METHOD])" in st
    oauth = _block("    async def _async_finish_reauth")
    assert "self._retitle(self._reauth_entry, AUTH_METHOD_OAUTH)" in oauth
    retitle = _block("    def _retitle")
    assert (
        "retitled_entry(entry.title, entry.data.get(CONF_NAME), auth_method)" in retitle
    )


def test_the_create_path_and_the_helper_agree():
    # The helper's suffixes are the ones the create path writes.
    assert 'title += " (OAuth)"' in CONFIG_FLOW
    assert 'title += " (SmartThings)"' in CONFIG_FLOW
