"""Picture mode -> IP Control ``pictureModeControl`` value.

The TV takes OCF names — the SmartThings ids without "mode" — which are the
same in every menu language. They come from the id whenever one is known;
only the untranslated "FILMMAKER MODE" and the four remote-key modes are ever
read from a label.
"""

import importlib.util
from pathlib import Path
import unittest

# Loaded straight from its file, like test_picture_mode_keys: the module is pure
# standard library and importing it through the package would pull in the whole
# integration.
_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "samsungtv_smart"
    / "picture_mode_keys.py"
)
_spec = importlib.util.spec_from_file_location("picture_mode_keys", _MODULE_PATH)
_pmk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pmk)

ip_value = _pmk.picture_mode_ip_value
ip_base = _pmk.picture_mode_ip_base
ip_variant = _pmk.picture_mode_ip_variant
slug = _pmk.picture_mode_slug


class TestSmartThingsId(unittest.TestCase):
    """The id wins, whatever the label says."""

    def test_the_mode_prefix_is_stripped(self):
        for mode_id, expected in (
            ("modeDynamic", "Dynamic"),
            ("modeMovie", "Movie"),
            ("modeFilmmakerMode", "FilmmakerMode"),
            ("modeNatural", "Natural"),
            # The 2018 set of #206 names its Dynamisk mode this way; it is a
            # predefined OCF value, so it is sent as is.
            ("modeEntertainment", "Entertainment"),
        ):
            with self.subTest(mode_id=mode_id):
                self.assertEqual(ip_value("anything", mode_id), expected)

    def test_a_label_in_any_language_follows_the_id(self):
        self.assertEqual(ip_value("Cinéma", "modeMovie"), "Movie")
        self.assertEqual(ip_value("Naturel", "modeNatural"), "Natural")


class TestVerbatim(unittest.TestCase):
    """OCF names, their option keys and hand-typed ids pass through."""

    def test_ocf_names_and_variants(self):
        for value in ("Dynamic", "Eco", "FilmmakerMode", "CAL-DAY", "StandardHDR"):
            with self.subTest(value=value):
                self.assertEqual(ip_value(value), value)

    def test_option_keys(self):
        self.assertEqual(ip_value("filmmaker_mode"), "FilmmakerMode")
        self.assertEqual(ip_value("movie"), "Movie")
        self.assertEqual(ip_value("cal_night"), "CAL-NIGHT")

    def test_a_typed_smartthings_id(self):
        self.assertEqual(ip_value("modeFilmmakerMode"), "FilmmakerMode")


class TestLabels(unittest.TestCase):
    """Labels only where they cannot be wrong."""

    def test_filmmaker_is_not_translated_by_samsung(self):
        for name in ("FILMMAKER MODE", "Filmmaker Mode", "filmmaker"):
            with self.subTest(name=name):
                self.assertEqual(ip_value(name), "FilmmakerMode")

    def test_the_four_remote_key_modes(self):
        cases = {
            "Dynamic": ["Dynamique", "Dynamisch", "Dinamica"],
            "Standard": ["Estándar", "Standaard"],
            "Movie": ["Cinéma", "Película", "Film"],
            "Eco": ["Éco", "Öko"],
        }
        for expected, names in cases.items():
            for name in names:
                with self.subTest(name=name):
                    self.assertEqual(ip_value(name), expected)

    def test_no_guess_without_an_id(self):
        # A translated Natural has no remote-key rule and is not guessed.
        for name in ("", "   ", "Naturel", "Natürlich", "Game", "Ambient"):
            with self.subTest(name=name):
                self.assertIsNone(ip_value(name))


class TestVariantsAndKeys(unittest.TestCase):
    def test_base_of_a_variant(self):
        self.assertEqual(ip_base("StandardHDR"), "Standard")
        self.assertEqual(ip_base("FilmmakerModeHDR"), "FilmmakerMode")
        self.assertEqual(ip_base("Movie"), "Movie")
        # Not a known mode: left alone rather than cut at a guessed suffix.
        self.assertEqual(ip_base("GamePCHDR"), "GamePCHDR")

    def test_variant_retry_follows_what_the_tv_reports(self):
        self.assertEqual(ip_variant("Movie", "StandardHDR"), "MovieHDR")
        self.assertIsNone(ip_variant("Movie", "Standard"))
        self.assertIsNone(ip_variant("Movie", None))
        self.assertIsNone(ip_variant("MovieHDR", "StandardHDR"))

    def test_option_keys_are_valid_translation_keys(self):
        for value in _pmk.IP_PICTURE_MODES:
            with self.subTest(value=value):
                self.assertRegex(slug(value), r"^[a-z0-9_]+$")
        self.assertEqual(slug("FilmmakerMode"), "filmmaker_mode")
        self.assertEqual(slug("CAL-DAY"), "cal_day")


if __name__ == "__main__":
    unittest.main()
