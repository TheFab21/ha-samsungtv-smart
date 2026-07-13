"""Sidecar map: skip unchanged files; never touch Samsung-Store / other-tools art."""
import json


def test_needs_upload_new_file():
    import _dedup
    sidecar = {}
    assert _dedup.needs_upload("cat.jpg", mtime=100.0, sidecar=sidecar) is True


def test_needs_upload_unchanged_file_skipped():
    import _dedup
    sidecar = {"cat.jpg": {"content_id": "MY-F0001", "modified": 100.0}}
    assert _dedup.needs_upload("cat.jpg", mtime=100.0, sidecar=sidecar) is False


def test_needs_upload_changed_mtime():
    import _dedup
    sidecar = {"cat.jpg": {"content_id": "MY-F0001", "modified": 100.0}}
    assert _dedup.needs_upload("cat.jpg", mtime=200.0, sidecar=sidecar) is True


def test_is_protected_samsung_store_and_other_tools():
    import _dedup
    assert _dedup.is_protected("SAM-F0042") is True   # Samsung Store
    assert _dedup.is_protected("MY-F0007") is True     # another tool's art
    # Our own uploads use plain content ids we recorded ourselves — not protected.
    assert _dedup.is_protected("") is False


def test_sidecar_roundtrip(tmp_path):
    import _dedup
    p = str(tmp_path / "sidecar.json")
    data = {"cat.jpg": {"content_id": "abc", "modified": 1.0}}
    _dedup.save_sidecar(p, data)
    assert _dedup.load_sidecar(p) == data
    assert json.loads((tmp_path / "sidecar.json").read_text()) == data


def test_load_sidecar_missing_returns_empty(tmp_path):
    import _dedup
    assert _dedup.load_sidecar(str(tmp_path / "nope.json")) == {}
