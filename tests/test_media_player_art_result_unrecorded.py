"""media_player.frame_art_last_result must stay out of the recorder.

art_available stores the whole artwork list in that attribute. On a 2025
Frame with 180 artworks it is ~42 kB, and the recorder then logs

    State attributes for media_player.the_frame exceed maximum size of
    16384 bytes. This can cause database performance issues; Attributes
    will not be stored

96 times in one log window — and drops every attribute of the entity for
those states, so source, app_id and sound mode were missing from history
too. FrameArtSensor already excludes its last_service_result; the media
player did not.

Read from the source with ast, so the check needs no Home Assistant.
"""

import ast
import json
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "samsungtv_smart"
    / "media_player.py"
)
ATTRIBUTE = "frame_art_last_result"
RECORDER_LIMIT = 16384


def _class(name: str) -> ast.ClassDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found")


def _unrecorded(cls: ast.ClassDef) -> set[str]:
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_unrecorded_attributes"
            for t in node.targets
        ):
            call = node.value
            assert isinstance(call, ast.Call) and call.func.id == "frozenset"
            return set(ast.literal_eval(call.args[0]))
    return set()


def test_art_result_is_excluded_from_the_recorder():
    assert ATTRIBUTE in _unrecorded(_class("SamsungTVDevice"))


def test_the_excluded_name_is_the_one_the_entity_publishes():
    # A rename of the attribute must not silently leave the exclusion behind.
    assert f'data["{ATTRIBUTE}"]' in SOURCE.read_text(encoding="utf-8")


def test_a_real_artwork_list_is_past_the_recorder_limit():
    # The shape art_available returns per artwork, from a 2025 Frame.
    item = {
        "content_id": "SAM-S10005408",
        "category_id": "MY-C0004",
        "slideshow": "false",
        "matte_id": "modernthin_antique",
        "portrait_matte_id": "shadowbox_antique",
        "width": 3840,
        "height": 2160,
        "image_date": "",
        "content_type": "server",
    }
    result = {"service": "art_available", "count": 180, "artwork": [item] * 180}
    assert len(json.dumps(result)) > RECORDER_LIMIT
