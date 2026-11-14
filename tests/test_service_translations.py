"""Service names and descriptions are translatable and match services.yaml."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "samsungtv_smart"
SERVICES = yaml.safe_load((ROOT / "services.yaml").read_text())
STRINGS = json.loads((ROOT / "strings.json").read_text())


def _clean(text):
    return " ".join(str(text).split())


def test_every_service_and_field_has_a_reference_string():
    # The UI showed service texts in English only: no "services" section
    # existed, so no language could translate them.
    services = STRINGS["services"]
    assert set(services) == set(SERVICES)
    for name, spec in SERVICES.items():
        assert set(services[name].get("fields", {})) == set(spec.get("fields") or {})


def test_reference_strings_match_services_yaml():
    # services.yaml stays the fallback; the English strings must not drift.
    drift = []
    for name, spec in SERVICES.items():
        ref = STRINGS["services"][name]
        if ref["name"] != spec["name"]:
            drift.append(f"{name}: name")
        if ref["description"] != _clean(spec["description"]):
            drift.append(f"{name}: description")
        for field, fspec in (spec.get("fields") or {}).items():
            fref = ref["fields"][field]
            if fref["name"] != fspec["name"]:
                drift.append(f"{name}.{field}: name")
            if fref["description"] != _clean(fspec["description"]):
                drift.append(f"{name}.{field}: description")
    assert not drift, drift


def _select_selectors():
    for name, spec in SERVICES.items():
        for field, fspec in (spec.get("fields") or {}).items():
            select = (fspec.get("selector") or {}).get("select")
            if select:
                yield f"{name}.{field}", select


def test_translated_selects_have_a_label_for_every_option():
    # A select with a translation_key shows its raw values ("3min", "2")
    # in any language whose file lacks the label.
    selectors = STRINGS["selector"]
    missing = []
    for where, select in _select_selectors():
        key = select.get("translation_key")
        if key is None:
            continue
        labels = selectors.get(key, {}).get("options", {})
        for option in select["options"]:
            if option not in labels:
                missing.append(f"{where}: {key}.{option}")
    assert not missing, missing


def test_translated_selects_use_plain_values():
    # A label in services.yaml would override the translation.
    labelled = [
        where
        for where, select in _select_selectors()
        if select.get("translation_key")
        and any(isinstance(o, dict) for o in select["options"])
    ]
    assert not labelled, labelled
