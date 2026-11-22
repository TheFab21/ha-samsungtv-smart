"""The frame-crop-upload card: bundled, registered, and its crop geometry.

The card crops, pans, zooms and rotates a picture to the panel's shape in the
browser, then posts it to the same endpoint as samsung-art-upload-card. Its
geometry lives in a delimited pure-helpers region that
tests/js/frame_crop_helpers.test.mjs evaluates under Node.
"""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "samsungtv_smart"
CARD = COMPONENT / "www" / "frame-crop-upload-card.js"
JS_TEST = ROOT / "tests" / "js" / "frame_crop_helpers.test.mjs"


def test_the_card_is_bundled_with_the_integration():
    assert CARD.is_file()


def test_the_card_is_registered_as_a_lovelace_resource():
    src = (COMPONENT / "__init__.py").read_text()
    assert '_register_bundled_card(hass, "frame-crop-upload-card.js")' in src


def test_the_card_posts_to_the_upload_endpoint_with_its_fields():
    src = CARD.read_text()
    assert '"/api/samsungtv_smart/art_upload"' in src
    for field in ('"entity_id"', '"matte_id"', '"file"'):
        assert f"form.append({field}" in src, f"multipart field missing: {field}"


def test_the_card_does_not_redefine_itself():
    # A manually added copy of the card may load first; the guard keeps the
    # second definition from throwing.
    assert 'if (!customElements.get("frame-crop-upload-card"))' in CARD.read_text()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_crop_geometry_behaviour():
    result = subprocess.run(
        ["node", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
