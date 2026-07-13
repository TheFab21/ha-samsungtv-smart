"""Perceptual dedup: TV re-encodes uploads, so exact hashes never match."""
import io

from PIL import Image


def _img(color, fmt="PNG", size=(400, 300)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return buf.getvalue()


def _reencoded(color):
    # Same picture, re-saved as JPEG (what the TV does) -> bytes differ, image same.
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), color).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def test_same_image_reencoded_is_duplicate():
    import _dedup
    a = _img((120, 80, 40))
    b = _reencoded((120, 80, 40))
    assert _dedup.perceptual_diff(a, b) <= 1.0
    assert _dedup.is_duplicate(a, b) is True


def test_different_images_not_duplicate():
    import _dedup
    a = _img((10, 10, 10))
    b = _img((240, 240, 240))
    assert _dedup.perceptual_diff(a, b) > 1.0
    assert _dedup.is_duplicate(a, b) is False
