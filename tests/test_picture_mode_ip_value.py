"""Tests for the picture mode -> IP Control pictureModeControl value resolution."""

import importlib.util
import unittest
from pathlib import Path

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

picture_mode_ip_value = _pmk.picture_mode_ip_value
IP_DYNAMIC = _pmk.IP_DYNAMIC
IP_STANDARD = _pmk.IP_STANDARD
IP_MOVIE = _pmk.IP_MOVIE
IP_ECO = _pmk.IP_ECO
IP_FILMMAKER = _pmk.IP_FILMMAKER
IP_NATURAL = _pmk.IP_NATURAL


class TestVerbatim(unittest.TestCase):
    """IP Control's own values pass through untouched."""

    def test_known_values(self):
        for value in (
            IP_DYNAMIC,
            IP_STANDARD,
            IP_MOVIE,
            IP_ECO,
            IP_FILMMAKER,
            IP_NATURAL,
        ):
            with self.subTest(value=value):
                self.assertEqual(picture_mode_ip_value(value), value)

    def test_hdr_variants_read_back_from_the_tv(self):
        # The getter reports "StandardHDR" while HDR content plays; the select
        # must be able to write that back as is.
        self.assertEqual(picture_mode_ip_value("StandardHDR"), "StandardHDR")
        self.assertEqual(picture_mode_ip_value("MovieHDR"), "MovieHDR")

    def test_unknown_camel_case_is_not_passed_through(self):
        # Only the known vocabulary is verbatim; a stray localized name that
        # happens to be ASCII must still go through the name rules.
        self.assertEqual(picture_mode_ip_value("Dynamique"), IP_DYNAMIC)
        self.assertIsNone(picture_mode_ip_value("GameHDR"))


class TestSmartThingsIds(unittest.TestCase):
    """The SmartThings mode id wins whenever one is known."""

    def test_ids_resolve(self):
        for mode_id, expected in (
            ("modeDynamic", IP_DYNAMIC),
            ("modeStandard", IP_STANDARD),
            ("modeMovie", IP_MOVIE),
            ("modeEco", IP_ECO),
            ("modeFilmmaker", IP_FILMMAKER),
            ("modeNatural", IP_NATURAL),
        ):
            with self.subTest(mode_id=mode_id):
                self.assertEqual(picture_mode_ip_value("whatever", mode_id), expected)


class TestLocalisedNames(unittest.TestCase):
    """Menu labels in other languages, as the SmartThings select hands them over."""

    def test_names(self):
        cases = {
            IP_DYNAMIC: ["Dynamic", "Dynamique", "Dynamisk", "Dynamický"],
            IP_STANDARD: ["Standard", "Estándar", "Štandardný"],
            IP_MOVIE: ["Movie", "Cinéma", "Film", "Película"],
            IP_ECO: ["Eco", "Éco", "Öko"],
            IP_FILMMAKER: ["FILMMAKER MODE", "Filmmaker Mode", "filmmaker"],
            IP_NATURAL: ["Natural", "Naturel", "Natürlich", "Naturale", "Prirodzený"],
        }
        for expected, names in cases.items():
            for name in names:
                with self.subTest(name=name):
                    self.assertEqual(picture_mode_ip_value(name), expected)

    def test_filmmaker_beats_movie(self):
        # "Filmmaker" contains "film": it must never resolve to Movie.
        self.assertEqual(picture_mode_ip_value("FILMMAKER MODE"), IP_FILMMAKER)

    def test_natural_is_not_movie(self):
        # The WS resolver maps English "Natural" to the Movie key for legacy
        # reasons; IP Control has a real Natural value, so it must not inherit that.
        self.assertEqual(picture_mode_ip_value("Natural"), IP_NATURAL)


class TestRefusals(unittest.TestCase):
    """Nothing is sent when the mode cannot be named."""

    def test_unknown_names(self):
        for name in ("", "   ", "Ambient", "Game", "Sports"):
            with self.subTest(name=name):
                self.assertIsNone(picture_mode_ip_value(name))


if __name__ == "__main__":
    unittest.main()
