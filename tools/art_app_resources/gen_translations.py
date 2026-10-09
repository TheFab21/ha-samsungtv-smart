"""Build entity translations from the Frame art-app's own text resources.

Usage: python3 gen_translations.py <out.json from extract_resources.py> \
           custom_components/samsungtv_smart/translations \
           custom_components/samsungtv_smart/strings.json
"""
import json, sys, os
TV = json.load(open(sys.argv[1]))
OUT = sys.argv[2]  # translations dir
STRINGS = sys.argv[3]

LANGS = {  # HA language code -> art-app locale
    "en": "en-US", "fr": "fr-FR", "it": "it-IT", "es": "es-ES", "pt-BR": "pt-BR",
    "hu": "hu-HU", "de": "de-DE", "nl": "nl-NL", "pl": "pl-PL", "sv": "sv-SE",
    "da": "da-DK", "fi": "fi-FI", "cs": "cs-CZ", "sk": "sk-SK", "sl": "sl-SI",
    "hr": "hr-HR", "ro": "ro-RO", "bg": "bg-BG", "el": "el-GR", "ru": "ru-RU",
    "uk": "uk-UA", "tr": "tr-TR", "lt": "lt-LT", "lv": "lv-LV", "et": "et-EE",
    "ca": "ca-ES", "pt": "pt-PT", "ja": "ja-JP", "ko": "ko-KR",
    "zh-Hans": "zh-CN", "zh-Hant": "zh-TW", "he": "he-IL", "ar": "ar-AE",
    "th": "th-TH", "vi": "vi-VN", "id": "id-ID",
}
# Wire id -> resource id, from MatteControl.dicMatteTypeSID / dicMatteColorSID.
MATTE_TYPE = {
    "none": "COM_TV_SID_FRAMETV20_NO_MAT",
    "modernthin": "TV_SID_FRAME_MODERN_MAT_THIN",
    "modern": "COM_TV_SID_FRAMETV20_MODERN_MAT",
    "modernwide": "TV_SID_FRAME_MODERN_MAT_WIDE",
    "flexible": "TV_SID_FRAME_CAJUL_MODERN_MAT_AUTO",
    "shadowbox": "COM_TV_SID_FRAMETV20_SHADOWBOX_MAT",
    "panoramic": "TV_SID_FRAMETV20_PANORAMIC_MAT",
    "triptych": "TV_SID_FRAMETV20_TRIPTYCH_MAT",
    "mix": "TV_SID_FRAMETV20_MIXED_MAT",
    "squares": "TV_SID_FRAMETV20_SQUARES_MAT",
}
MATTE_COLOR = {
    "black": "COM_SID_BLACK_KR_FRAMETV",
    "neutral": "COM_TV_SID_FRAMETV_NEUTRAL_KR_FRAMEMOBILE",
    "antique": "COM_TV_SID_FRAMETV_ANTIQUE",
    "warm": "TV_SID_HOTEL_WARM",
    "polar": "COM_TV_SID_FRAMETV_POLAR",
    "sand": "TV_SID_AMBIENT_SAND",
    "seafoam": "COM_TV_SID_FRAMETV_SLATE",
    "sage": "COM_TV_SID_FRAMETV_SAGE",
    "burgandy": "COM_TV_SID_FRAMETV_BURANDY",
    "navy": "COM_IDS_CLR_NAVY",
    "apricot": "TV_SID_AMBIENT_APRICOT",
    "byzantine": "TV_SID_AMBIENT_BYZANTINE",
    "lavender": "TV_SID_AMBIENT_LAVENDER",
    "redorange": "COM_SID_ORANGE",
    "skyblue": "TV_SID_AMBIENT_SKY_BLUE",
    "turquoise": "TV_SID_AMBIENT_TURQUOISE",
}
TIMER = {
    "5": ("TV_SID_AMBIENT_5_MINUTES", None),
    "15": ("TV_SID_FRAMETV20_15_MINUTES_LENGTH10", None),
    "30": ("TV_SID_GENERAL_30_MINUTES", None),
    "60": ("COM_SID_1_HOUR", None),
    "120": ("COM_TV_SID_MIX_HOURS_KR_BLANK", 2),
    "180": ("COM_TV_SID_MIX_HOURS_KR_BLANK", 3),
    "240": ("COM_TV_SID_MIX_HOURS_KR_BLANK", 4),
}
# Not in the art-app resources: written by hand where we can vouch for them.
MANUAL = {
    "en": {"timer": "Motion Timer", "off": "Off", "always": "Always"},
    "fr": {"timer": "Minuterie de mouvement", "off": "Désactivé", "always": "Toujours"},
    "it": {"timer": "Timer movimento", "off": "Disattivato", "always": "Sempre"},
    "es": {"timer": "Temporizador de movimiento", "off": "Desactivado", "always": "Siempre"},
    "pt-BR": {"timer": "Temporizador de movimento", "off": "Desligado", "always": "Sempre"},
    "hu": {"timer": "Mozgásérzékelő időzítő", "off": "Ki", "always": "Mindig"},
    "de": {"timer": "Bewegungstimer", "off": "Aus", "always": "Immer"},
    "nl": {"timer": "Bewegingstimer", "off": "Uit", "always": "Altijd"},
}
EN_NAMES = {"matte_type": "Matte Type", "matte_color": "Matte Color", "art_mode": "Art Mode"}

def tv(loc, key, arg=None):
    v = TV[loc].get(key)
    if not v:
        v = TV["en-US"][key]
    v = v.strip()
    if arg is not None:
        v = v.replace("{0}", str(arg))
    assert "{" not in v and "}" not in v, (loc, key, v)
    return v

def entity_block(lang, loc):
    man = MANUAL.get(lang, {})
    names = EN_NAMES if lang == "en" else {
        "matte_type": tv(loc, "COM_TV_SID_FRAMETV20_MAT"),
        "matte_color": tv(loc, "TV_SID_FRAME_CASEP_MAT_COLOR"),
        "art_mode": tv(loc, "COM_MAPP_SID_FRAMETV_ART_MODE"),
    }
    timer_states = {k: tv(loc, r, a) for k, (r, a) in TIMER.items()}
    for k in ("off", "always"):
        if k in man:
            timer_states[k] = man[k]
    timer = {"state": dict(sorted(timer_states.items(), key=lambda kv: (not kv[0].isdigit(), int(kv[0]) if kv[0].isdigit() else 0)))}
    if "timer" in man:
        timer = {"name": man["timer"], **timer}
    return {
        "select": {
            "matte_type": {"name": names["matte_type"], "state": {k: tv(loc, r) for k, r in MATTE_TYPE.items()}},
            "matte_color": {"name": names["matte_color"], "state": {k: tv(loc, r) for k, r in MATTE_COLOR.items()}},
            "art_motion_timer": timer,
        },
        "switch": {"art_mode": {"name": names["art_mode"]}},
    }

def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v

def write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

for lang, loc in LANGS.items():
    path = os.path.join(OUT, f"{lang}.json")
    data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    merge(data.setdefault("entity", {}), entity_block(lang, loc))
    write(path, data)
    if lang == "en":
        st = json.load(open(STRINGS, encoding="utf-8"))
        merge(st.setdefault("entity", {}), entity_block(lang, loc))
        write(STRINGS, st)
print("ok", len(LANGS))
