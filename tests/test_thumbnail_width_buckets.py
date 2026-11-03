"""The unauthenticated thumbnail endpoint must not cache unbounded variants.

It caches one JPEG per (image, width) and accepted any width from 64 to 1024,
so a stranger who can reach Home Assistant (e.g. through a public URL) could
have it encode and store ~960 copies of every image under www. Widths now snap
up to a handful of sizes.

http_thumbnail.py needs Home Assistant to import, so the helper is exec'd from
source.
"""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
SOURCE = (ROOT / "http_thumbnail.py").read_text()


def _load():
    start = SOURCE.index("_DEFAULT_W = 400")
    end = SOURCE.index("class SamsungTVThumbnailView(")
    namespace = {}
    exec(compile(SOURCE[start:end], "http_thumbnail.py", "exec"), namespace)
    return namespace


NS = _load()
snap = NS["snap_width"]


class SnapWidthTest(unittest.TestCase):
    def test_every_width_lands_on_a_bucket(self):
        produced = {snap(w) for w in range(-50, 5000)}
        self.assertEqual(produced, set(NS["WIDTH_BUCKETS"]))

    def test_rounds_up_so_the_card_gets_at_least_what_it_asked(self):
        self.assertEqual(snap(400), 400)
        self.assertEqual(snap(401), 640)
        self.assertEqual(snap(129), 256)
        self.assertEqual(snap(1), 128)

    def test_garbage_and_huge_values(self):
        self.assertEqual(snap("abc"), 400)
        self.assertEqual(snap(None), 400)
        self.assertEqual(snap("99999"), 1024)

    def test_the_card_default_is_a_bucket(self):
        self.assertIn(400, NS["WIDTH_BUCKETS"])


if __name__ == "__main__":
    unittest.main()
