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
