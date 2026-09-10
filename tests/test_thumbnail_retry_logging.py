"""A thumbnail that is still being retried is not a failure yet.

After an upload the TV takes 30 s to several minutes to generate the
thumbnail, and _retry_new_thumbnail waits it out with a back-off. Every fetch
that lands too early used to log "Could not download thumbnail … Failed after
3 attempts" at WARNING — 11 of them in one hour on a maintainer's install,
and all five images involved ended up with their thumbnail.

The warning now belongs to the retry chain, which is the only place that
knows nothing further will be tried.
"""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1] / "custom_components" / "samsungtv_smart"
MEDIA_PLAYER = (ROOT / "media_player.py").read_text()


def _block(source: str, start: str, end: str) -> str:
    begin = source.index(start)
    return source[begin : source.index(end, begin)]


class RetryChainTest(unittest.TestCase):
    """_retry_new_thumbnail owns the in-flight marker and the final verdict."""

    def setUp(self):
        self.block = _block(
            MEDIA_PLAYER,
            "    async def _retry_new_thumbnail",
            "    async def async_art_delete",
        )

    def test_the_content_id_is_marked_before_the_first_sleep(self):
        marked = self.block.index("self._thumbnail_retry_pending.add(content_id)")
        slept = self.block.index("await asyncio.sleep(delay)")
        self.assertLess(marked, slept)

    def test_the_marker_is_cleared_in_a_finally(self):
        # An exception or an early return must not leave the id marked, or its
        # later failures would be silently downgraded forever.
        self.assertIn("finally:", self.block)
        finally_body = self.block[self.block.index("finally:") :]
        self.assertIn("self._thumbnail_retry_pending.discard(content_id)", finally_body)

    def test_success_still_returns_early_and_logs_info(self):
        success = self.block[: self.block.index("finally:")]
        self.assertIn("is now available (delayed retry)", success)
        self.assertIn("return", success)

    def test_giving_up_warns_and_says_how_long_it_waited(self):
        tail = self.block[self.block.index("finally:") :]
        self.assertIn("self._log.warning(", tail)
        self.assertIn("never generated it", tail)
        self.assertIn("len(retry_delays)", tail)

    def test_the_final_warning_is_outside_the_finally_block(self):
        # It must not fire on the success path, which returns from inside try.
        discard = self.block.index("self._thumbnail_retry_pending.discard")
        warn = self.block.index("self._log.warning(")
        self.assertLess(discard, warn)


class FetchSiteTest(unittest.TestCase):
    """The per-attempt failure defers to the retry chain."""

    def setUp(self):
        self.block = _block(
            MEDIA_PLAYER,
            "            # All retries failed.",
            "        except Exception as ex:",
        )

    def test_it_chooses_debug_while_a_retry_is_pending(self):
        self.assertIn("content_id in self._thumbnail_retry_pending", self.block)
        self.assertIn("self._log.debug", self.block)

    def test_it_still_warns_when_nothing_will_retry(self):
        self.assertIn("else self._log.warning", self.block)

    def test_the_message_is_unchanged(self):
        self.assertIn(
            'log("Could not download thumbnail for %s: %s", content_id, error_msg)',
            self.block,
        )

    def test_the_result_is_still_an_error_for_callers(self):
        # Only the log level is conditional; the returned value must not be.
        self.assertIn(
            'result = {"error": error_msg, "content_id": content_id}', self.block
        )


class InitTest(unittest.TestCase):
    def test_the_pending_set_is_initialised(self):
        self.assertIn("self._thumbnail_retry_pending: set[str] = set()", MEDIA_PLAYER)

    def test_it_is_only_mutated_by_the_retry_chain(self):
        mutations = re.findall(
            r"_thumbnail_retry_pending\.(add|discard|clear|pop)", MEDIA_PLAYER
        )
        self.assertEqual(sorted(mutations), ["add", "discard"])


if __name__ == "__main__":
    unittest.main()
