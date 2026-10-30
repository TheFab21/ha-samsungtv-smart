"""Map a picture mode to what selects it locally: a remote key, or an IP Control value.

Samsung TVs accept a handful of dedicated remote keys that jump straight to a
picture mode. That is the only path left when the SmartThings cloud refuses
``setPictureMode`` — which happens on models whose cloud link cannot actuate
them (#197) and on HDMI sources with content protection.

Matching is deliberately layered, most reliable first:

1. **The internal mode id** (``modeStandard``, ``modeDynamic``, …). Identical in
   every language, so this is the answer whenever the TV exposes
   ``supportedPictureModesMap``.
2. **The display name, normalised.** Many TVs — including every model that only
   offers ``custom.picturemode`` — expose no id map at all, leaving the
   localized name as the only handle. Accents and case are stripped and the
   name is matched on a *prefix*, because Samsung's translations of the same
   four modes overwhelmingly share a stem: Dynamic / Dynamique / Dynamisch /
   Dynamisk / Dinamico / Dinâmico / Dinamik / Dynamiczny / Dynamický all begin
   with ``dynam`` or ``dinam``. Exact per-language tables were tried first and
   failed exactly where they were needed: a Slovak TV (#197) and a Norwegian
   one (#206) each got a key for only the one mode that happened to be spelled
   like English.

Two deliberate refusals, because sending the wrong mode is worse than sending
none:

* **FILMMAKER MODE** has no remote key, and contains "film" — it must be
  rejected before the movie rule, or it would silently select Movie.
* **Natural** has no key of its own. The legacy table mapped it to the *Movie*
  key; that is preserved for the exact English word so existing setups do not
  change behaviour, but it is deliberately not extended to other languages.
"""

from __future__ import annotations

import re
import unicodedata

KEY_DYNAMIC = "KEY_DYNAMIC"
KEY_STANDARD = "KEY_STANDARD"
KEY_MOVIE = "KEY_MOVIE1"
KEY_ECO = "KEY_ESAVING"

# Internal ids, which are the same on every TV and in every language.
_ID_KEYS = {
    "modedynamic": KEY_DYNAMIC,
    "modestandard": KEY_STANDARD,
    "modemovie": KEY_MOVIE,
    "modeeco": KEY_ECO,
}

# Names that must never produce a key, checked before everything else.
# Substrings, since "FILMMAKER MODE" carries a trailing word.
_NO_KEY_MARKERS = ("filmmaker",)

# Legacy exact names kept verbatim from the original table, so behaviour on
# setups that already worked is untouched. "natural" -> Movie is dubious (see
# module docstring) but pre-existing; it is not generalised below.
_EXACT_NAMES = {
    "cinema (etalonne)": KEY_MOVIE,
    "natural": KEY_MOVIE,
}

# Prefixes of the localized names, normalised (lowercase, accents stripped).
# Ordered: the first prefix that matches wins, so more specific stems must come
# before shorter ones that would also match them.
_NAME_PREFIXES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("dynam", "dinam"), KEY_DYNAMIC),
    (("standar", "estandar", "standaard", "normal", "vakio"), KEY_STANDARD),
    (("movie", "film", "cine", "pelicul", "elokuv"), KEY_MOVIE),
    (("eco", "eko", "oko", "energi"), KEY_ECO),
)


def _normalise(value: str) -> str:
    """Lowercase and strip accents, so "Dinâmico" and "Dynamický" compare."""
    decomposed = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def picture_mode_ws_key(display_name: str, mode_id: str = "") -> str | None:
    """Return the remote key for a picture mode, or None if there is none.

    ``mode_id`` is the TV's internal id when known (from
    ``supportedPictureModesMap``); ``display_name`` is what the user picked.
    """
    if mode_id:
        key = _ID_KEYS.get(_normalise(mode_id))
        if key:
            return key

    name = _normalise(display_name)
    if not name:
        return None
    if any(marker in name for marker in _NO_KEY_MARKERS):
        return None
    if name in _EXACT_NAMES:
        return _EXACT_NAMES[name]
    for prefixes, key in _NAME_PREFIXES:
        if name.startswith(prefixes):
            return key
    return None


# IP Control ``pictureModeControl`` takes the TV's OCF values: the SmartThings
# mode ids without their "mode" prefix (modeFilmmakerMode <-> FilmmakerMode,
# modeEntertainment <-> Entertainment). They are the same in every menu
# language — the tvcontrol library of an EU Frame 2025 firmware maps each
# internal mode to an OCF value and, separately, to its translated label — so
# they are taken from the id whenever one is known, never guessed from a label.

# The firmware's predefined OCF values. A TV accepts the subset it offers on
# the current input and answers -32002 for the rest: a Frame 2024 (QE55LS03D)
# and a 2026 S95H both took Dynamic, Standard, Movie, FilmmakerMode and Eco and
# refused Natural, Entertainment, Relax, Intelligent, CAL-DAY and CAL-NIGHT.
IP_PICTURE_MODES = (
    "Dynamic",
    "Standard",
    "Natural",
    "Movie",
    "FilmmakerMode",
    "Eco",
    "Entertainment",
    "Relax",
    "Intelligent",
    "CAL-DAY",
    "CAL-NIGHT",
)
# The getter reports these variants while HDR or PC-classified content plays
# (StandardHDR, GamePCHDR, ...). Longest first, so "PCHDR" wins over "HDR".
_IP_VARIANT_SUFFIXES = ("PCHDR", "HDR", "PC")

_IP_BY_KEY = {
    KEY_DYNAMIC: "Dynamic",
    KEY_STANDARD: "Standard",
    KEY_MOVIE: "Movie",
    KEY_ECO: "Eco",
}


def _split_ip_variant(value: str) -> tuple[str, str]:
    """Split ``StandardHDR`` into ``("Standard", "HDR")``; no suffix -> ``""``."""
    for suffix in _IP_VARIANT_SUFFIXES:
        base = value[: -len(suffix)]
        if value.endswith(suffix) and base in IP_PICTURE_MODES:
            return base, suffix
    return value, ""


def picture_mode_ip_base(value: str) -> str:
    """Return the mode behind an HDR / PC variant (``StandardHDR`` -> ``Standard``)."""
    return _split_ip_variant(value)[0]


# With AI Mode on, a TV offers two picture modes and nothing else: Samsung
# calls them AI Optimised, which adapts to the room and the content, and AI
# Customisation, which keeps saved settings per kind of content. Over
# ``pictureModeControl`` the first is the predefined OCF name ``Intelligent``;
# the second has no name of its own and is reported as a generated id, which
# follows what is on screen rather than the app. Measured on a Frame Pro
# (26_RSM_FTV, T-RSMHDEUC-0100-1078.6) in #352:
#
#   Gen_589828  0x9_0004  SDR video, in three different apps alike
#   Gen_589829  0x9_0005  HDR video
#   Gen_327705  0x5_0019  the home screen, and the seconds between two apps
#   Gen_589826  0x9_0002  one afternoon only, unexplained
#
# Setting matters as much as reading. ``Intelligent`` was accepted and held,
# and so was the generated id the TV was reporting at that moment — but
# another generated id, for a screen that was not up, answered ``-32002``. So
# the only value that selects AI Customisation is the one the TV is reporting
# right then, which is why the select stores it afresh on every poll and never
# a fixed name.
#
# 8.15.0 reported every generated id as ``Intelligent``, on the strength of
# one poll seen as AI Mode came on (probably the TV passing through AI
# Optimised). That showed AI Customisation under AI Optimised's name, and
# picking it moved the TV from one to the other. The two are kept apart now.
#
# Not anchored at the end: the getter decorates the predefined modes with HDR
# / PC suffixes and may decorate these the same way, which is unobserved.
_IP_GENERATED_MODE = re.compile(r"^Gen_\d+")

# The option key the select offers for the whole generated family, since no
# single value names it.
PICTURE_MODE_GENERATED_OPTION = "ai_customization"


def picture_mode_ip_is_generated(value: str) -> bool:
    """Return whether the TV reported a generated AI Customisation id."""
    return bool(_IP_GENERATED_MODE.match(value))


def picture_mode_ip_variant(value: str, current: str | None) -> str | None:
    """Return ``value`` with the suffix the TV currently reports, if any.

    While HDR content plays the getter reports e.g. ``StandardHDR``. Whether
    the setter then wants the plain name or the variant is unmeasured, so the
    variant is kept as a second try for when the plain name is refused.
    """
    if not current or _split_ip_variant(value)[1]:
        return None
    suffix = _split_ip_variant(current)[1]
    return f"{value}{suffix}" if suffix else None


def picture_mode_slug(value: str) -> str:
    """Return the option key of an OCF value (``FilmmakerMode`` -> ``filmmaker_mode``).

    Home Assistant translation keys are lower-case snake case, so the select
    offers these and translates them; the OCF value is what the TV receives.
    """
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    return re.sub(r"[^a-z0-9]+", "_", snake.lower()).strip("_")


_IP_BY_SLUG = {picture_mode_slug(value): value for value in IP_PICTURE_MODES}


def picture_mode_ip_value(display_name: str, mode_id: str = "") -> str | None:
    """Return the IP Control ``pictureModeControl`` value for a picture mode.

    Most reliable first:

    1. the SmartThings id (``modeMovie`` -> ``Movie``), the same in every
       language;
    2. an OCF value as is (``Movie``, ``StandardHDR``), its option key
       (``filmmaker_mode``) or a SmartThings id typed by hand;
    3. the label: ``FILMMAKER MODE`` is not translated by Samsung, and the four
       basic modes go through the remote-key name rules above — no second set
       of per-language rules. Anything else (a translated "Natural", "Game")
       returns None rather than a guess.
    """
    if mode_id.startswith("mode") and len(mode_id) > len("mode"):
        return mode_id[len("mode") :]

    raw = display_name.strip()
    if not raw:
        return None
    if picture_mode_ip_base(raw) in IP_PICTURE_MODES:
        return raw
    if raw.lower() in _IP_BY_SLUG:
        return _IP_BY_SLUG[raw.lower()]
    if raw.startswith("mode") and raw[len("mode") :] in IP_PICTURE_MODES:
        return raw[len("mode") :]

    if "filmmaker" in _normalise(raw):
        return "FilmmakerMode"
    key = picture_mode_ws_key(raw)
    return _IP_BY_KEY.get(key) if key else None
