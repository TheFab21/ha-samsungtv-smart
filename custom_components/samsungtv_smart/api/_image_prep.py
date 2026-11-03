"""Normalise images into something a Frame TV will actually decode.

The Frame is far pickier than a browser. Progressive JPEGs, unusual chroma
subsampling, CMYK, 16-bit or paletted samples, oversized EXIF blobs — and above
all **images larger than the panel** — are happily *stored* by the TV and then
fail to render: the artwork shows up as a grey rectangle with no thumbnail, and
the TV never emits ``image_added``, so the upload also looks like it failed.

The SmartThings app never hits this because it re-encodes before sending, so we
do the same, once, for every upload path (the card, ``art_upload`` and
``art_upload_batch``).

Pillow — and the optional ``pillow-heif`` opener for iPhone HEIC — are imported
lazily so a missing codec degrades this to a clean error instead of breaking the
import of the whole integration. Everything here is blocking: call it from an
executor.
"""

from __future__ import annotations

import io

# Panel size assumed when the TV has not reported its resolution yet.
DEFAULT_PANEL = (3840, 2160)


def panel_size(resolution: str | None) -> tuple[int, int]:
    """Parse a TV-reported resolution ("3840x2160") into a (w, h) tuple.

    Falls back to 4K on anything unparseable, so a TV that has not answered the
    REST device-info call yet still gets a sane cap.
    """
    try:
        width, height = (int(part) for part in str(resolution).lower().split("x", 1))
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_PANEL
    return (width, height) if width > 0 and height > 0 else DEFAULT_PANEL


def fit_box(
    image_size: tuple[int, int],
    panel: tuple[int, int],
    rotation: str | None = None,
) -> tuple[int, int]:
    """Bounding box for an image on a panel hung as ``rotation``.

    The box follows how the Frame is mounted, not the image. A landscape-hung
    3840x2160 Frame stores a portrait photo taller than 2160 without an error
    but never displays it: select_image, and a slideshow of its category, get
    no answer at all. Measured on a 2025 Frame (QE43LS03FAUXXH, landscape):
    1952x2164 and 2160x3837 uploads stayed off screen, 3837x2160 and the same
    photo as SmartThings sent it (1623x1800) showed at once. So:

    - ``"portrait"`` (the art app's get_current_rotation): the panel's short
      edge across, long edge down - 2160x3840 - so a portrait-mounted Frame
      keeps a portrait photo's full detail;
    - ``"landscape"``, or unknown: 3840x2160. A portrait photo is shrunk to
      2160 tall, which every Frame displays; unknown errs on the side that
      shows the image rather than the one that can lose it.

    ``image_size`` is kept for callers; the box no longer depends on it.
    """
    del image_size  # the panel's mounting decides the box, not the image
    long_edge, short_edge = max(panel), min(panel)
    if rotation == "portrait":
        return (short_edge, long_edge)
    return (long_edge, short_edge)


def normalise_for_frame(
    data: bytes,
    panel: tuple[int, int] = DEFAULT_PANEL,
    rotation: str | None = None,
) -> tuple[bytes, str]:
    """Return (jpeg_bytes, ".jpg") the TV can decode. Blocking — use executor.

    Encoding stays deliberately ordinary — quality 92 with standard 4:2:0
    chroma — because that is what cameras, phones and the SmartThings app emit,
    and what the TV reliably accepts; maximal settings (quality 100, 4:4:4)
    were refused by the Frame. EXIF orientation is applied before the metadata
    is dropped, so portrait photos are not sent sideways.
    """
    from PIL import Image, ImageOps  # noqa: PLC0415 - lazy: keep PIL out of import

    try:
        import pillow_heif  # noqa: PLC0415 - optional HEIC codec

        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 - HEIC just stays unsupported without it
        pass

    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)  # honour rotation, then drop EXIF
        rgb = img.convert("RGB")
        box = fit_box(rgb.size, panel, rotation)
        if rgb.width > box[0] or rgb.height > box[1]:
            rgb.thumbnail(box, Image.LANCZOS)
        out = io.BytesIO()
        rgb.save(
            out,
            format="JPEG",
            quality=92,
            subsampling="4:2:0",
            progressive=False,  # baseline only: TVs choke on progressive
            optimize=False,
        )
    return out.getvalue(), ".jpg"
