"""Map a picture mode to the remote key that selects it locally.

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


# IP Control ``pictureModeControl`` values, keyed by the WS key of the same
# mode so the two resolvers share one set of name rules. Unlike the remote
# keys, IP Control has values for Filmmaker and Natural as well.
IP_DYNAMIC = "Dynamic"
IP_STANDARD = "Standard"
IP_MOVIE = "Movie"
IP_ECO = "Eco"
IP_FILMMAKER = "FilmmakerMode"
IP_NATURAL = "Natural"

_IP_BY_KEY = {
    KEY_DYNAMIC: IP_DYNAMIC,
    KEY_STANDARD: IP_STANDARD,
    KEY_MOVIE: IP_MOVIE,
    KEY_ECO: IP_ECO,
}
_IP_VALUES = frozenset(
    (IP_DYNAMIC, IP_STANDARD, IP_MOVIE, IP_ECO, IP_FILMMAKER, IP_NATURAL)
)

_IP_NATURAL_PREFIXES = ("natur", "prirod", "přiroz", "natuur", "luonno")


def picture_mode_ip_value(display_name: str, mode_id: str = "") -> str | None:
    """Return the IP Control ``pictureModeControl`` value for a picture mode.

    Accepts the SmartThings id (``modeMovie``), a localized display name
    (``Cinéma``), or an IP Control value verbatim (``StandardHDR``), which is
    passed through so a value read back from the TV can be written back as is.
    Returns None when the mode cannot be named safely.
    """
    raw = display_name.strip()
    if not raw:
        return None
    # Verbatim IP Control values pass through, including the HDR variants the
    # getter reports ("StandardHDR"), which have no other handle.
    if raw in _IP_VALUES or (raw.endswith("HDR") and raw[:-3] in _IP_VALUES):
        return raw

    name = _normalise(raw)
    if "filmmaker" in name or _normalise(mode_id) == "modefilmmaker":
        return IP_FILMMAKER
    if name.startswith(_IP_NATURAL_PREFIXES) or _normalise(mode_id) == "modenatural":
        return IP_NATURAL
    key = picture_mode_ws_key(raw, mode_id)
    if key is None:
        return None
    return _IP_BY_KEY.get(key)
