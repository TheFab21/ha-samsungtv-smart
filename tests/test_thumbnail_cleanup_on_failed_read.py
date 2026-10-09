"""A failed artwork-list read must not wipe the local thumbnail cache.

Observed on 192.168.1.31: an automation ran art_get_thumbnails_batch
(personal_only, cleanup_orphans) 20 min after an HA restart while the Frame was
asleep and its art channel was not answering. available() returned [] for the
failed read, the service took that as "the TV has no personal photos" and
deleted all 16 local personal thumbnails.

available(strict=True) now returns None when the TV did not answer, and the
batch service deletes nothing in that case. A genuine empty list still cleans
up (e.g. after a factory reset). Checked on the source (HA needed to import).
"""

from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
ART = (ROOT / "api" / "art.py").read_text()
MP = (ROOT / "media_player.py").read_text()


def _block(text, header):
    start = text.index(header)
    return text[start : text.index("\n    async def ", start + 10)]


class StrictAvailableTest(unittest.TestCase):
    def setUp(self):
        self.block = _block(ART, "    async def available(")

    def test_strict_returns_none_on_failure(self):
        self.assertIn("strict: bool = False", self.block)
        self.assertIn("failed: list | None = None if strict else []", self.block)
        self.assertEqual(self.block.count("return failed"), 3)

    def test_default_behaviour_is_unchanged(self):
        # Non-strict callers (artwork count, art_available) still get [].
        self.assertIn("else []", self.block)


class BatchServiceDeletesNothingOnFailedReadTest(unittest.TestCase):
    def setUp(self):
        self.block = _block(MP, "    async def async_art_get_thumbnails_batch(")

    def test_every_list_read_is_strict(self):
        self.assertEqual(self.block.count("self._art_api.available("), 4)
        self.assertEqual(self.block.count("strict=True"), 4)

    def test_none_returns_before_any_cleanup(self):
        guard = self.block.index("if artwork_list is None:")
        cleanup = self.block.index("await self._cleanup_orphan_thumbnails(")
        self.assertLess(guard, cleanup)
        self.assertIn("return result", self.block[guard:cleanup])

    def test_the_decision_reproduces(self):
        def deletes_everything(artwork_list):
            if artwork_list is None:
                return False
            return not {a.get("content_id") for a in artwork_list}

        self.assertFalse(deletes_everything(None))  # TV asleep -> keep cache
        self.assertTrue(deletes_everything([]))  # really empty -> clean


if __name__ == "__main__":
    unittest.main()
