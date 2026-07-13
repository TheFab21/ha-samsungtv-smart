"""Perceptual dedup + content_id sidecar map for the Samsung Art client.

SPDX-License-Identifier: LGPL-3.0
Dedup approach adapted from NickWaterton async_art_gallery_web.py (LGPL).
"""
from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageFilter

_THUMB_SIZE = (384, 216)


def _fingerprint(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as img:
        return (
            img.convert("L")
            .resize(_THUMB_SIZE)
            .filter(ImageFilter.GaussianBlur(2))
        )


def perceptual_diff(img_a: bytes, img_b: bytes) -> float:
    """Return mean per-pixel grayscale difference (0-255) of two images."""
    fa = _fingerprint(img_a)
    fb = _fingerprint(img_b)
    diff = ImageChops.difference(fa, fb)
    hist = diff.histogram()
    total = sum(i * n for i, n in enumerate(hist))
    count = sum(hist)
    return total / count if count else 0.0


def is_duplicate(img_a: bytes, img_b: bytes, threshold: float = 1.0) -> bool:
    """True if two images are perceptually identical (survives TV re-encode)."""
    return perceptual_diff(img_a, img_b) <= threshold
