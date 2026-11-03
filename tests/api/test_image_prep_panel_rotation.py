"""Uploads are fitted to how the Frame hangs, not to the photo's shape.

A landscape-hung 2025 Frame (QE43LS03FAUXXH) stored portrait uploads taller
than its 2160 px panel without an error and never displayed them: 1952x2164
and 2160x3837 got no answer to select_image (nor to a slideshow of their
category), while 3837x2160 and the same photo as SmartThings sent it
(1623x1800) showed at once. fit_box used to size every portrait photo for a
portrait-mounted Frame (2160x3840); it now follows the reported rotation and
falls back to the landscape box, which every Frame displays.
"""

import io

import pytest
from PIL import Image

from samsungtv_smart_test_api import _image_prep as prep

PANEL = (3840, 2160)


@pytest.mark.parametrize(
    ("rotation", "box"),
    [
        ("landscape", (3840, 2160)),
        (None, (3840, 2160)),
        ("portrait", (2160, 3840)),
    ],
)
@pytest.mark.parametrize("image", [(1952, 2164), (3000, 4000), (4000, 3000)])
def test_box_follows_the_mounting_not_the_photo(image, rotation, box):
    assert prep.fit_box(image, PANEL, rotation) == box


def _jpeg(size):
    out = io.BytesIO()
    Image.new("RGB", size, (120, 80, 160)).save(out, format="JPEG")
    return out.getvalue()


def _size(data):
    with Image.open(io.BytesIO(data)) as img:
        return img.size


@pytest.mark.parametrize("rotation", ["landscape", None])
def test_portrait_photo_fits_a_landscape_frame(rotation):
    # The photo that stayed off screen: now 2160 tall at most.
    data, _ = prep.normalise_for_frame(_jpeg((1952, 2164)), PANEL, rotation)
    width, height = _size(data)
    assert height <= 2160 and width <= 3840
    assert (width, height) == (1948, 2160)


def test_tall_phone_photo_on_a_landscape_frame():
    data, _ = prep.normalise_for_frame(_jpeg((3000, 4000)), PANEL, "landscape")
    assert _size(data) == (1620, 2160)


def test_portrait_frame_keeps_full_portrait_detail():
    data, _ = prep.normalise_for_frame(_jpeg((3000, 4000)), PANEL, "portrait")
    assert _size(data) == (2160, 2880)


def test_landscape_photo_unchanged_by_the_fix():
    data, _ = prep.normalise_for_frame(_jpeg((4000, 3000)), PANEL, "landscape")
    assert _size(data) == (2880, 2160)
