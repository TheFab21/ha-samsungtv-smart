"""Artwork identification pipeline for Samsung Frame TVs (v8.4).

Two-stage, cache-first identification of the artwork currently shown on a
Frame TV:

1. **Reverse image search** — Google Cloud Vision *Web Detection* turns the
   thumbnail into concrete candidate titles/artists/pages pulled from the real
   web. This is the step a bare LLM cannot do (no vision model performs reverse
   image search; they identify "from memory" and hallucinate on obscure works).
2. **LLM confirmation** — an LLM (Anthropic, OpenAI or Gemini) is handed those
   candidates and the image, and asked to confirm ONLY if a candidate matches
   what it actually sees, otherwise return ``identified: false``. Feeding it
   real candidates collapses the hallucination surface: it verifies concrete
   names instead of guessing.

Everything is cached so the same artwork is never identified twice. The cache
key is chosen by how much we trust the Samsung id:

* ``SAM-*`` — global Art-Store catalogue id, stable across TV resets → the id
  is the key.
* anything else (``MY-*`` personal uploads, blank, unknown) — the id is a
  recyclable local slot number that a TV reset can re-assign to a different
  image, so it must NOT key the cache (a stale hit would show the wrong
  metadata). The key is the image content instead (``IMG-<sha256[:16]>``); the
  thumbnails we cache are the ones the integration downloaded, so they are
  byte-stable and hash reliably.

This module is provider-agnostic and free of Home Assistant entity code: it
takes the image bytes and the resolved config, and returns a plain dict. The
coordinator / sensor / service wiring lives elsewhere.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import time
from typing import Any

from aiohttp import ClientError, ClientSession

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .art_files import art_dir
from .const import (
    ART_CACHE_TTL_HIT,
    ART_CACHE_TTL_MISS,
    CONF_ART_IDENTIFY_ENABLE,
    CONF_ART_IDENTIFY_PERSONAL,
    CONF_ART_LLM_API_KEY,
    CONF_ART_LLM_MODEL,
    CONF_ART_LLM_PROVIDER,
    CONF_ART_VISION_API_KEY,
    DATA_ART_CACHE,
    DEFAULT_ART_LLM_MODEL,
    DEFAULT_ART_LLM_PROVIDER,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class VisionError(Exception):
    """Reverse-image-search (Google Vision) call failed."""


class LLMError(Exception):
    """LLM confirmation call failed or returned unparseable output."""


class ModelUnavailableError(LLMError):
    """The configured model was rejected by the provider (retired/typo).

    A subclass so existing handlers keep catching it, while callers that care
    can tell a configuration problem apart from a transport failure.
    """


_VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)

_CACHE_STORE_VERSION = 1

# Bumped whenever the prompt or the candidate handling changes in a way that
# could turn a past failure into a success. Cached *misses* from an older
# revision are ignored so the improvement applies on the next artwork change;
# cached hits are untouched, since a confirmed identification stays valid.
#   1 — original "confirm a candidate or nothing" prompt
#   2 — candidate de-noising + identification from the model's own knowledge
#   3 — real image media type sent; prompt no longer implies "old master
#       painting", which made the model withhold graphic/contemporary works
#   4 — a lone "not identified" with no candidate is re-sampled once
#   5 — web entities explained as unordered fragments (title / museum /
#       artist), which the model may combine to pin down a work
_PIPELINE_REVISION = 5
_HTTP_TIMEOUT = 45  # seconds per external call
# Output token budget for the confirmation reply. Must fit the whole JSON —
# title + three prose fields in every LANGS language — or the reply is
# truncated and fails to parse. 700 was fine single-language; 5 languages need
# far more headroom.
_LLM_MAX_TOKENS = 8000

# Gemini 2.5+/3.x spend "thinking" tokens from the SAME budget as the answer,
# and reason by default. With a modest cap the reply is cut off mid-JSON —
# which surfaces as a cryptic "Expecting ',' delimiter" rather than a quota
# error (issue #188). Disable thinking for this task: it is a constrained
# extraction against a supplied candidate list, not a reasoning problem.
_GEMINI_THINKING_BUDGET = 0

# Languages the metadata is produced in, so each viewer can be shown the
# artwork description in their own UI language (the Lovelace card picks by the
# browser/user language). Keep 'en' first — it's the universal fallback.
LANGS = ("en", "fr", "es", "it", "pt-BR")

# Language-independent fields (kept once): identity + confidence.
TOP_LEVEL_KEYS = (
    "identified",
    "confidence",
    "matched_candidate",
    "artist",
    "date",
    "suggested_search_query",
)
# Fields produced per language (prose + the possibly-translated title).
TRANSLATED_FIELDS = (
    "title",
    "artwork_description",
    "artist_biography",
    "visual_description",
)
# Full set stored/returned: the top-level keys plus a 'translations' dict
# {lang: {translated field: value}}.
RESULT_KEYS = (*TOP_LEVEL_KEYS, "translations")


def derive_key(content_id: str | None, img_bytes: bytes) -> str:
    """Return the cache key for an artwork.

    ``SAM-*`` ids are global and stable → trusted as the key. Everything else
    is keyed by image content, so a recycled local id can never yield a stale
    hit for a different image.
    """
    if content_id and content_id.startswith("SAM-"):
        return content_id
    return "IMG-" + hashlib.sha256(img_bytes).hexdigest()[:16]


class ArtIdentifyCache:
    """Persistent, TTL'd cache of identification results (one per config entry).

    Backed by Home Assistant's ``Store`` (JSON under ``.storage/``): survives
    restarts and container updates, needs no extra dependency, and is plenty
    for the few dozen artworks that actually rotate on a Frame.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        # _v2 = multilingual schema; the old single-language cache file is left
        # untouched but no longer read, so entries re-identify once into all
        # languages instead of surfacing a partial (English-only) result.
        self._store: Store = Store(
            hass, _CACHE_STORE_VERSION, f"samsungtv_smart_art_cache_v2_{entry_id}"
        )
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded = False

    async def async_load(self) -> None:
        """Load the cache from disk once."""
        if self._loaded:
            return
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._data = stored
        self._loaded = True

    def get(self, key: str) -> dict[str, Any] | None:
        """Return a non-expired cached result, or None.

        A hit (identified) is kept effectively forever; a miss is retried after
        ``ART_CACHE_TTL_MISS``, or immediately if it was produced by an older
        pipeline revision. Expired entries return None so the caller re-runs
        the pipeline.
        """
        entry = self._data.get(key)
        if not entry:
            return None
        if not entry.get("identified") and entry.get("_rev") != _PIPELINE_REVISION:
            # A failure recorded by an older prompt/candidate logic says nothing
            # about the current one. Without this, improving identification had
            # no visible effect for up to ART_CACHE_TTL_MISS (14 days) on
            # exactly the artworks the improvement was written for.
            return None
        ttl = ART_CACHE_TTL_HIT if entry.get("identified") else ART_CACHE_TTL_MISS
        if time.time() - entry.get("_fetched_at", 0) > ttl:
            return None
        result = {k: entry.get(k) for k in RESULT_KEYS}
        result["source"] = "cache"
        return result

    async def async_put(self, key: str, result: dict[str, Any]) -> None:
        """Store a fresh result (hit or miss) and persist it. Errors are not cached."""
        entry = {k: result.get(k) for k in RESULT_KEYS}
        entry["_fetched_at"] = time.time()
        entry["_rev"] = _PIPELINE_REVISION
        self._data[key] = entry
        await self._store.async_save(self._data)

    async def async_invalidate(self, key: str) -> None:
        """Drop one entry (used by a forced re-identification)."""
        if self._data.pop(key, None) is not None:
            await self._store.async_save(self._data)


async def async_vision_web_detection(
    session: ClientSession, api_key: str, img_b64: str
) -> dict[str, list[str]]:
    """Run Google Cloud Vision Web Detection on a base64 image.

    Returns the useful, de-noised bits: the best-guess label(s) (often
    literally "Title — Artist"), the higher-confidence web entities, and the
    titles of pages the image appears on (galleries/stock listings usually
    carry the real title + credit). Raises on transport/API failure so the
    caller can avoid caching a non-answer.
    """
    body = {
        "requests": [
            {
                "image": {"content": img_b64},
                "features": [{"type": "WEB_DETECTION", "maxResults": 10}],
            }
        ]
    }
    async with session.post(
        f"{_VISION_URL}?key={api_key}", json=body, timeout=_HTTP_TIMEOUT
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise VisionError(f"Vision API {resp.status}: {text[:200]}")
        data = await resp.json()
    wd = (data.get("responses") or [{}])[0].get("webDetection", {}) or {}
    return {
        "best_guess": [
            b.get("label") for b in wd.get("bestGuessLabels", []) if b.get("label")
        ],
        "entities": [
            e["description"]
            for e in wd.get("webEntities", [])
            if e.get("description") and e.get("score", 0) > 0.5
        ][:8],
        "pages": [
            p.get("pageTitle")
            for p in wd.get("pagesWithMatchingImages", [])
            if p.get("pageTitle")
        ][:5],
    }


# Web entities Vision returns for practically every painting. They carry no
# identifying power, but they pad the candidate list and make a genuinely
# useful entity ("The Kimono", "Basket of Fruit") look like one item in a
# wall of noise.
_GENERIC_ENTITIES = frozenset(
    {
        "art",
        "artist",
        "artwork",
        "acrylic paint",
        "canvas",
        "cartoon",
        "clip art",
        "doodle",
        "drawing",
        "graphic design",
        "line art",
        "logo",
        "sketch",
        "design",
        "digital art",
        "digital illustration",
        "fine art",
        "graphics",
        "illustration",
        "illustrator",
        "image",
        "landscape painting",
        "modern art",
        "oil painting",
        "paint",
        "painter",
        "painting",
        "photograph",
        "photography",
        "picture",
        "portrait",
        "poster",
        "printmaking",
        "still life",
        "visual arts",
        "watercolor painting",
    }
)

# Page titles that match these are tutorials/listicles the image merely
# resembles — Vision returns them when it has NOT found the picture anywhere.
# Feeding them to the LLM as "the image appears on these pages" is actively
# misleading.
_NOISE_PAGE_RE = re.compile(
    r"how to|tutorial|for beginners|beginners guide|tips|step by step|"
    r"masterclass|guide to|the basics|basics of|painting technique|lesson|course|"
    r"learn to|teaching you|level up|what is |youtube",
    re.IGNORECASE,
)


def denoise_candidates(candidates: dict[str, list[str]]) -> dict[str, list[str]]:
    """Drop the generic filler from Vision's reply and promote the specifics.

    Vision answers a failed reverse image search with plausible-looking
    filler: entities like "Painting"/"Art" and pages like "How To Master
    Painting With Zero Experience". Passed through verbatim, that filler both
    buries the occasional real lead and makes an unidentified image look
    well-sourced. Entities that name something concrete are moved to the
    front; noise pages are removed entirely so an empty ``pages`` list
    honestly signals "found nowhere".
    """
    entities = candidates.get("entities") or []
    specific = [e for e in entities if e.strip().lower() not in _GENERIC_ENTITIES]
    generic = [e for e in entities if e.strip().lower() in _GENERIC_ENTITIES]
    pages = [p for p in (candidates.get("pages") or []) if not _NOISE_PAGE_RE.search(p)]
    best_guess = [
        b
        for b in (candidates.get("best_guess") or [])
        if b.strip().lower() not in _GENERIC_ENTITIES
    ]
    return {
        "best_guess": best_guess,
        "entities": specific + generic,
        "pages": pages,
    }


# Base64 prefixes of the magic bytes of the formats a Frame thumbnail can be
# in. Every thumbnail is saved as ``current.jpg`` whatever the TV actually
# sent (the Art API reports the real type in its header), so the extension is
# not evidence of the content.
_B64_MAGIC = (
    ("/9j/", "image/jpeg"),
    # Only the bytes that are constant in the signature: base64 encodes three
    # bytes per four characters, so a longer prefix would start depending on
    # the payload and stop matching.
    ("iVBORw0K", "image/png"),
    ("R0lG", "image/gif"),
    ("UklGR", "image/webp"),
)


def media_type_from_b64(img_b64: str) -> str:
    """Return the real image media type, sniffed from the encoded bytes.

    All three providers are told the media type explicitly; announcing
    ``image/jpeg`` for a PNG is at best undefined behaviour and at worst a
    rejected request. Falls back to JPEG, which is what a Frame thumbnail
    usually is.
    """
    for prefix, media_type in _B64_MAGIC:
        if img_b64.startswith(prefix):
            return media_type
    return "image/jpeg"


def _has_usable_candidate(candidates: dict[str, list[str]]) -> bool:
    """True if the reverse search produced anything the LLM can confirm.

    After de-noising, a candidate set made only of generic entities carries no
    identifying power: the model is answering from memory alone, and a "no" is
    then a judgement call rather than a rejection of a concrete name.
    """
    if candidates.get("best_guess"):
        return True
    return any(
        e.strip().lower() not in _GENERIC_ENTITIES
        for e in (candidates.get("entities") or [])
    )


def _build_llm_prompt(candidates: dict[str, list[str]]) -> str:
    """Prompt that hands the reverse-search candidates to the LLM for checking.

    Asks for the descriptive fields in every language of ``LANGS`` so each
    viewer can be shown the metadata in their own UI language from a single
    call (cheaper and consistent vs one call per language).
    """
    langs = ", ".join(LANGS)
    return (
        "This image is the thumbnail of the artwork currently displayed on a "
        "Samsung The Frame TV. A reverse image search returned these CANDIDATES "
        "(possibly wrong):\n"
        f"- Best guess: {' / '.join(candidates.get('best_guess') or []) or '(none)'}\n"
        f"- Web entities: {', '.join(candidates.get('entities') or []) or '(none)'}\n"
        f"- Page titles: {' | '.join(candidates.get('pages') or []) or '(none)'}\n\n"
        "The web entities are unordered FRAGMENTS, not a formatted answer: one "
        "may be the title, another the museum that holds the work, another the "
        "artist, mixed in with generic labels. Read them together — a title "
        "plus a holding institution is often enough to pin down a specific "
        "work you know.\n\n"
        "Your task, in this order:\n"
        "1. If a candidate is consistent with what you actually SEE in the "
        'image, confirm it: set "identified": true, name it in '
        '"matched_candidate", and use your normal confidence. A fragment does '
        "not have to arrive complete: if the fragments plus the image let you "
        "name one specific work you know, that IS a confirmation — fill in the "
        "artist and date yourself, and say which fragment you matched.\n"
        "2. The candidate list is often empty or generic, because reverse "
        "image search regularly fails on artwork. In that case you MAY still "
        "identify the work from your OWN knowledge, whenever you genuinely "
        'recognise what you are looking at. Then set "identified": true, '
        'leave "matched_candidate": null, and cap "confidence" at 0.6 to mark '
        "that no external source corroborates it.\n"
        "   The Samsung Art Store is not limited to old master paintings: it "
        "sells photography, prints, posters, graphic design, street art, "
        "cartoons, calligraphy and contemporary illustration. Do NOT withhold "
        "an identification because the image looks like an icon, a doodle, "
        "clip art or a logo rather than a traditional painting — that is "
        "exactly what a large part of the catalogue is. A signature motif you "
        "can name together with its artist (for instance an artist's "
        "recurring emblematic figure) counts as recognising the work: give "
        "the motif's usual name as the title.\n"
        '3. If you do not recognise the work, set "identified": false and '
        "leave artist/date null. Recognising a STYLE, period or school alone "
        "is NOT recognising the work — do not guess a plausible title, and "
        "never attribute a piece to an artist merely because it looks like "
        "their manner. The test is whether you can name it, not whether it "
        "reminds you of someone.\n"
        "Do NOT invent facts. Separate the visual description from the factual "
        "identification. confidence is a number from 0 to 1. Always fill "
        "visual_description, even when the work is not identified.\n"
        f"Provide title, artwork_description, artist_biography and "
        f"visual_description in ALL these languages: {langs}. artist, date and "
        "suggested_search_query stay in one language (English). Translate the "
        "prose naturally, don't just transliterate.\n"
        "Reply with ONLY valid JSON (no prose, no markdown fences), schema:\n"
        '{"identified": false, "confidence": 0.0, "matched_candidate": null, '
        '"artist": null, "date": null, "suggested_search_query": null, '
        '"translations": {"en": {"title": null, "artwork_description": null, '
        '"artist_biography": null, "visual_description": ""}, '
        '"fr": {"title": null, "artwork_description": null, '
        '"artist_biography": null, "visual_description": ""}, '
        '"es": {...}, "it": {...}, "pt-BR": {...}}}'
    )


def _anthropic_output_schema() -> dict[str, Any]:
    """JSON Schema for the identification reply, in Anthropic's strict dialect.

    Built from the key tuples above so it can never drift from what
    ``_normalize_result`` expects. Three constraints the API enforces:
    ``additionalProperties: false`` on every object, no recursion, and no
    numeric/length keywords (``minimum``, ``minLength``, ...) — so the shape is
    expressed with types and ``required`` alone.

    The API also caps how many parameters may use a union type (a type array
    or ``anyOf``): one ``["string", "null"]`` per translated field per
    language came to 25 and was rejected with a 400. Translated fields are
    therefore plain strings, with an empty string meaning "unknown";
    ``_normalize_result`` already treats an empty value as missing and falls
    back to English.
    """
    translated = {
        "type": "object",
        "properties": {field: {"type": "string"} for field in TRANSLATED_FIELDS},
        "required": list(TRANSLATED_FIELDS),
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "identified": {"type": "boolean"},
            "confidence": {"type": ["number", "null"]},
            "matched_candidate": {"type": ["string", "null"]},
            "artist": {"type": ["string", "null"]},
            "date": {"type": ["string", "null"]},
            "suggested_search_query": {"type": ["string", "null"]},
            "translations": {
                "type": "object",
                "properties": {lang: translated for lang in LANGS},
                "required": list(LANGS),
                "additionalProperties": False,
            },
        },
        "required": [*TOP_LEVEL_KEYS, "translations"],
        "additionalProperties": False,
    }


def _parse_llm_json(raw: str) -> dict[str, Any]:
    """Extract the JSON object from an LLM reply, tolerating stray fences/prose.

    Raises a *diagnosable* error when the reply was cut short. A truncated
    answer used to surface as ``Expecting ',' delimiter: line 20 column 6``,
    which tells the user nothing about the actual cause (the model spent its
    token budget before finishing the JSON — see ``_GEMINI_THINKING_BUDGET``).
    """
    text = raw.strip()
    if not text:
        raise LLMError("empty LLM reply (model returned no text)")

    start = text.find("{")
    end = text.rfind("}")
    if start == -1:
        raise LLMError(f"no JSON object in LLM reply: {text[:200]}")

    if end < start:
        raise LLMError(
            "LLM reply was cut off before the JSON closed — the model ran out "
            f"of output tokens. Reply ended with: ...{text[-120:]!r}"
        )

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as ex:
        # An unbalanced brace count is the signature of a truncated reply,
        # as opposed to a genuinely malformed one.
        candidate = text[start : end + 1]
        if candidate.count("{") != candidate.count("}"):
            raise LLMError(
                "LLM reply was cut off mid-JSON — the model ran out of output "
                f"tokens ({ex}). Try a lighter model or a larger budget."
            ) from ex
        raise LLMError(f"could not parse LLM JSON ({ex}): {candidate[:200]}") from ex


async def async_llm_confirm(
    session: ClientSession,
    provider: str,
    api_key: str,
    model: str,
    img_b64: str,
    candidates: dict[str, list[str]],
) -> dict[str, Any]:
    """Ask the configured LLM to confirm/enrich against the candidates.

    Provider-agnostic wrapper over the Anthropic, OpenAI and Gemini vision
    chat APIs. Raises on transport/parse failure (not cached).
    """
    prompt = _build_llm_prompt(candidates)
    media_type = media_type_from_b64(img_b64)
    if provider == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": model,
            "max_tokens": _LLM_MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": img_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            # Structured outputs (GA, no beta header): the API constrains the
            # reply to this schema instead of us hoping the prompt was obeyed.
            # Dropped and retried without it on models that predate the feature
            # — see the 400 handling below.
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": _anthropic_output_schema(),
                }
            },
        }
        url = _ANTHROPIC_URL
    elif provider == "openai":
        headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        # max_completion_tokens (not the deprecated max_tokens) — the gpt-5 /
        # o-series models reject max_tokens outright; it works for gpt-4o too.
        # temperature is intentionally omitted: the reasoning models only
        # accept the default, and the reverse-search candidates + json_object
        # already constrain the answer, so determinism isn't needed here.
        body = {
            "model": model,
            "max_completion_tokens": _LLM_MAX_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{img_b64}"},
                        },
                    ],
                }
            ],
        }
        url = _OPENAI_URL
    elif provider == "gemini":
        headers = {"content-type": "application/json"}
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": media_type,
                                "data": img_b64,
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": _LLM_MAX_TOKENS,
                # camelCase is the documented REST spelling (the snake_case
                # form is the Python SDK's).
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingBudget": _GEMINI_THINKING_BUDGET},
            },
        }
        # Gemini takes the key as a query param, not a header.
        url = _GEMINI_URL_TMPL.format(model=model, key=api_key)
    else:
        raise LLMError(f"unknown LLM provider: {provider!r}")

    # Two attempts at most: the retry exists only to drop `output_config` for a
    # model that predates structured outputs. Everything else fails on the
    # first pass.
    for attempt in (1, 2):
        async with session.post(
            url, json=body, headers=headers, timeout=_HTTP_TIMEOUT
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                break

            text = await resp.text()
            # An older Claude rejects output_config with a 400. Drop it and
            # retry — the prompt still asks for the same JSON, so we degrade to
            # the previous contract instead of failing outright.
            if (
                attempt == 1
                and resp.status == 400
                and "output_config" in body
                and "output_config" in text
            ):
                _LOGGER.debug(
                    "Art identify: %s rejected structured outputs, retrying "
                    "with the prompt-only JSON contract",
                    model,
                )
                body.pop("output_config", None)
                continue

            # A retired or mistyped model is a configuration problem, not a
            # transient failure: retrying it forever just burns quota and
            # leaves a cryptic 404 in the log. Name the alternatives instead.
            if resp.status == 404:
                available = await async_list_models(session, provider, api_key)
                suggestion = (
                    f" Available models for this key include: "
                    f"{', '.join(available[:8])}."
                    if available
                    else ""
                )
                raise ModelUnavailableError(
                    f"{provider} model {model!r} is not available to this API "
                    f"key.{suggestion} Update it under Configure → Art "
                    f"Identification."
                )
            raise LLMError(f"{provider} API {resp.status}: {text[:200]}")

    _raise_if_truncated(provider, model, data)

    if provider == "anthropic":
        blocks = data.get("content") or []
        raw = next((b.get("text", "") for b in blocks if b.get("type") == "text"), "")
    elif provider == "gemini":
        parts = (
            (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [{}])
        )
        raw = "".join(p.get("text", "") for p in parts)
    else:
        raw = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    _LOGGER.debug("LLM (%s/%s) raw reply: %s", provider, model, raw[:600])
    parsed = _parse_llm_json(raw)
    return _normalize_result(parsed)


def _raise_if_truncated(provider: str, model: str, data: dict[str, Any]) -> None:
    """Fail loudly when the provider says it stopped at the token limit.

    Every provider reports this in its own field, and all three can hit it the
    same way: reasoning/thinking tokens are drawn from the reply budget, so a
    model that reasons a lot returns a JSON object cut mid-structure. Reading
    the provider's own signal is far more reliable than inferring it from a
    parse error after the fact (issue #188).
    """
    if provider == "anthropic":
        reason = data.get("stop_reason")
        truncated = reason == "max_tokens"
    elif provider == "gemini":
        reason = (data.get("candidates") or [{}])[0].get("finishReason")
        truncated = reason == "MAX_TOKENS"
    else:  # openai
        reason = (data.get("choices") or [{}])[0].get("finish_reason")
        truncated = reason == "length"

    if truncated:
        raise LLMError(
            f"{provider} model {model!r} hit its output limit "
            f"({_LLM_MAX_TOKENS} tokens) before finishing the JSON "
            f"(stop reason {reason!r}). Pick a lighter model — reasoning "
            f"models spend this budget thinking before they answer."
        )


def _normalize_result(parsed: dict[str, Any]) -> dict[str, Any]:
    """Coerce an LLM reply to the multilingual schema.

    Guarantees a ``translations`` dict with every ``LANGS`` entry present (each
    with the ``TRANSLATED_FIELDS`` keys), falling back to English for any
    language the model omitted so a viewer never gets an empty description.
    """
    result = {k: parsed.get(k) for k in TOP_LEVEL_KEYS}
    raw_tr = parsed.get("translations")
    if not isinstance(raw_tr, dict):
        raw_tr = {}
    en = raw_tr.get("en") if isinstance(raw_tr.get("en"), dict) else {}
    translations: dict[str, dict[str, Any]] = {}
    for lang in LANGS:
        block = raw_tr.get(lang) if isinstance(raw_tr.get(lang), dict) else {}
        translations[lang] = {
            field: block.get(field) or en.get(field) for field in TRANSLATED_FIELDS
        }
    result["translations"] = translations
    return result


async def async_identify(
    hass: HomeAssistant,
    session: ClientSession,
    cache: ArtIdentifyCache,
    content_id: str | None,
    image_bytes: bytes,
    *,
    vision_key: str,
    provider: str,
    llm_key: str,
    model: str,
    force: bool = False,
) -> dict[str, Any]:
    """Identify one artwork, cache-first.

    Returns a dict with the RESULT_KEYS plus ``source`` (``cache`` | ``fresh`` |
    ``error``). Transport/API errors return ``identified: false`` with
    ``source: error`` and are NOT cached, so the next artwork change retries.
    """
    await cache.async_load()
    key = derive_key(content_id, image_bytes)

    if force:
        _LOGGER.debug("Artwork %s: force=True, bypassing cache", key)
        await cache.async_invalidate(key)
    else:
        hit = cache.get(key)
        if hit is not None:
            _LOGGER.debug(
                "Artwork %s: cache HIT (identified=%s title=%s)",
                key,
                hit.get("identified"),
                hit.get("title"),
            )
            return hit
    _LOGGER.debug(
        "Artwork %s: cache miss — running pipeline (%d bytes, %s/%s)",
        key,
        len(image_bytes),
        provider,
        model,
    )

    img_b64 = base64.b64encode(image_bytes).decode()
    started = time.monotonic()
    try:
        candidates = denoise_candidates(
            await async_vision_web_detection(session, vision_key, img_b64)
        )
        _LOGGER.debug(
            "Artwork %s: Vision candidates best_guess=%s | entities=%s | pages=%s",
            key,
            candidates.get("best_guess"),
            candidates.get("entities"),
            candidates.get("pages"),
        )
        result = await async_llm_confirm(
            session, provider, llm_key, model, img_b64, candidates
        )
        if not result.get("identified") and not _has_usable_candidate(candidates):
            # No candidate to check against, so the answer rests entirely on
            # what the model recalls — and that is not deterministic. Observed
            # on one artwork with everything else held constant: four runs,
            # two "not identified", two correct (Keith Haring, confidence
            # 0.58-0.60). Temperature cannot be lowered to fix it, since the
            # reasoning models only accept the default. So take one more
            # sample: a second "no" is evidence, a lone "no" was a coin toss.
            # Only on failure, and only when there was nothing to confirm, so
            # the common paths cost exactly one call as before.
            _LOGGER.debug("Artwork %s: unidentified with no candidates — retrying", key)
            retry = await async_llm_confirm(
                session, provider, llm_key, model, img_b64, candidates
            )
            if retry.get("identified"):
                result = retry
    except (VisionError, LLMError, ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Artwork identification failed for %s: %s", key, err)
        return {
            **{k: None for k in RESULT_KEYS},
            "identified": False,
            "source": "error",
        }

    await cache.async_put(key, result)
    result["source"] = "fresh"
    _LOGGER.debug(
        "Artwork %s identified=%s title=%s artist=%s conf=%s in %.1fs (via %s/%s)",
        key,
        result.get("identified"),
        (result.get("translations") or {}).get("en", {}).get("title"),
        result.get("artist"),
        result.get("confidence"),
        time.monotonic() - started,
        provider,
        model,
    )
    return result


def _read_file_bytes(path: str) -> bytes:
    """Read a file's bytes (runs in the executor). Raises OSError if missing."""
    with open(path, "rb") as handle:
        return handle.read()


def get_shared_cache(hass: HomeAssistant, entry_id: str) -> ArtIdentifyCache:
    """Return the per-entry ArtIdentifyCache, creating it once.

    Shared between the manual service and the metadata sensor so both hit the
    same in-memory cache within a session.
    """
    store = hass.data[DOMAIN][entry_id]
    cache = store.get(DATA_ART_CACHE)
    if cache is None:
        cache = ArtIdentifyCache(hass, entry_id)
        store[DATA_ART_CACHE] = cache
    return cache


async def async_identify_for_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    content_id: str | None,
    *,
    cache: ArtIdentifyCache,
    force: bool = False,
) -> dict[str, Any]:
    """Resolve config + thumbnail for a config entry and run identification.

    The single entry point shared by the ``art_identify`` service and the
    metadata sensor. Returns the identification dict, or a dict with an
    ``error`` / ``skipped`` key describing why nothing ran (disabled, missing
    keys, personal artwork with the toggle off, thumbnail not downloaded yet).
    """
    data = entry.data
    if not data.get(CONF_ART_IDENTIFY_ENABLE):
        return {
            "error": "Art identification is disabled — enable it in the "
            "integration options (Configure → Art Identification)"
        }
    vision_key = data.get(CONF_ART_VISION_API_KEY)
    llm_key = data.get(CONF_ART_LLM_API_KEY)
    provider = data.get(CONF_ART_LLM_PROVIDER) or DEFAULT_ART_LLM_PROVIDER
    model = data.get(CONF_ART_LLM_MODEL) or DEFAULT_ART_LLM_MODEL.get(provider)
    if not vision_key or not llm_key:
        return {"error": "Vision and LLM API keys must be set in the options"}

    is_store = bool(content_id and content_id.startswith("SAM-"))
    _LOGGER.debug(
        "Art identify: content_id=%s store=%s provider=%s model=%s force=%s",
        content_id,
        is_store,
        provider,
        model,
        force,
    )
    if not is_store and not data.get(CONF_ART_IDENTIFY_PERSONAL):
        return {
            "skipped": "personal artwork identification is disabled",
            "content_id": content_id,
        }

    path = art_dir(hass, entry.entry_id, "current.jpg")
    try:
        image_bytes = await hass.async_add_executor_job(_read_file_bytes, path)
    except OSError as ex:
        return {
            "error": f"thumbnail not available yet ({ex}); wait for the Frame "
            "Art sensor to download current.jpg"
        }
    _LOGGER.debug("Art identify: read thumbnail %s (%d bytes)", path, len(image_bytes))

    session = async_get_clientsession(hass)
    result = await async_identify(
        hass,
        session,
        cache,
        content_id,
        image_bytes,
        vision_key=vision_key,
        provider=provider,
        llm_key=llm_key,
        model=model,
        force=force,
    )
    result["content_id"] = content_id
    return result


# --- Model discovery -------------------------------------------------------
#
# Hardcoding a model id gives it an expiry date: providers retire models and
# the integration then fails with a 404 for everyone who never touched the
# setting (issue #188, where the pinned gemini-2.5-flash became unavailable to
# new users). Ask the provider what it actually offers instead, the way Home
# Assistant's own Google Generative AI integration does.

_MODELS_URL = {
    "anthropic": "https://api.anthropic.com/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
}

# Preference order used to pick a default from the live list: cheap, fast,
# vision-capable models first. Matched as substrings, best match wins.
_MODEL_PREFERENCE = {
    "anthropic": ("haiku", "sonnet"),
    "openai": ("mini", "gpt-4o", "gpt-4"),
    "gemini": ("flash-lite", "flash"),
}

# Substrings marking models that cannot do the job (no vision, or a different
# modality entirely), so they never reach the picker.
_MODEL_EXCLUDE = (
    "embed",
    "tts",
    "whisper",
    "audio",
    "moderation",
    "image-",
    "dall-e",
    "realtime",
    "transcribe",
    "search",
    "veo",
    "imagen",
)


async def async_list_models(
    session: ClientSession, provider: str, api_key: str
) -> list[str]:
    """Return the model ids this key can actually use, best candidates first.

    Raises :class:`LLMError` when the key is rejected or the provider is
    unreachable, so the config flow can report a precise reason instead of
    silently offering an empty list.
    """
    url = _MODELS_URL.get(provider)
    if url is None:
        raise LLMError(f"unknown LLM provider: {provider!r}")

    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if provider == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION}
        params = {"limit": "1000"}
    elif provider == "openai":
        headers = {"Authorization": f"Bearer {api_key}"}
    else:  # gemini takes the key as a query param
        params = {"key": api_key, "pageSize": "1000"}

    async with session.get(
        url, headers=headers, params=params, timeout=_HTTP_TIMEOUT
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise LLMError(f"{provider} model list {resp.status}: {text[:200]}")
        data = await resp.json()

    names: list[str] = []
    if provider == "gemini":
        for item in data.get("models") or []:
            # Only models that can answer a generateContent call are usable.
            if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                continue
            # The API returns "models/gemini-x"; the request path wants the bare id.
            names.append(str(item.get("name", "")).removeprefix("models/"))
    else:  # anthropic and openai share the {"data": [{"id": ...}]} shape
        names = [str(item.get("id", "")) for item in data.get("data") or []]

    usable = [
        name
        for name in names
        if name and not any(bad in name.lower() for bad in _MODEL_EXCLUDE)
    ]
    return sort_models_by_preference(provider, usable)


def sort_models_by_preference(provider: str, models: list[str]) -> list[str]:
    """Order models so the cheapest sensible default comes first.

    Within a preference tier the provider's own order is kept (Anthropic
    returns newest first, and for the others alphabetical is stable enough),
    so a newly released haiku outranks last year's.
    """
    preference = _MODEL_PREFERENCE.get(provider, ())

    def rank(name: str) -> tuple[int, str]:
        lowered = name.lower()
        for index, token in enumerate(preference):
            if token in lowered:
                return (index, "")
        return (len(preference), lowered)

    return sorted(models, key=rank)


async def async_pick_default_model(
    session: ClientSession, provider: str, api_key: str
) -> str | None:
    """Best available model for a provider, or None if the list can't be read."""
    try:
        models = await async_list_models(session, provider, api_key)
    except (LLMError, ClientError, TimeoutError) as ex:
        _LOGGER.debug("Could not list %s models: %s", provider, ex)
        return None
    return models[0] if models else None
