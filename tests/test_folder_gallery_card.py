"""The folder-gallery card: one source of truth, and the folder-selector logic.

#303 asked for a folder selector in the card. The card ships once, bundled and
auto-registered by the integration (__init__.py); a stale 722-line duplicate
under www/community/ was removed so there is a single source of truth.
"""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
CARD = ROOT / "custom_components" / "samsungtv_smart" / "www" / "folder-gallery-card.js"
JS_TEST = ROOT / "tests" / "js" / "folder_gallery_helpers.test.mjs"


def test_the_card_is_bundled_with_the_integration():
    assert CARD.is_file()


def test_the_stale_duplicate_card_is_gone():
    # The divergent manual-install copy must not come back: it is not the one
    # the integration serves, so it silently went out of date.
    assert not (ROOT / "www" / "community" / "folder-gallery-card").exists()


def test_the_card_declares_the_folder_selector():
    src = CARD.read_text()
    for needle in (
        "_buildFolderSets",
        "_folderSelectorHtml",
        "group_by_subfolder",
        "this._config.folders",
        "fgcGroupBySubfolder",
    ):
        assert needle in src, f"folder-selector code missing: {needle}"


def test_the_editor_exposes_group_by_subfolder():
    src = CARD.read_text()
    # The visual editor must render the checkbox and persist it.
    assert 'id="group_by_subfolder"' in src
    assert "cfg.group_by_subfolder" in src
    assert "ed_group_sub" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pure_helpers_behaviour():
    result = subprocess.run(
        ["node", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
