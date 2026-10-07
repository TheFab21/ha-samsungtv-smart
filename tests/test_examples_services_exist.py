"""Every samsungtv_smart.* service used in examples/ must exist in services.yaml.

The example configs are copy-pasted by users, so a service name that does not
exist is a real bug: Home Assistant answers "service not found". scripts.yaml
and lovelace-folder-gallery.yaml referenced art_slideshow, art_current and
set_art_mode, none of which are real services.
"""

from pathlib import Path
import re

ROOT = Path(__file__).parents[1]
SERVICES_YAML = ROOT / "custom_components" / "samsungtv_smart" / "services.yaml"
EXAMPLES = ROOT / "examples"

# A top-level "name:" key in services.yaml is a service; nested keys are
# indented, so anchor at column 0.
_DEFINED = set(re.findall(r"^([a-z_][a-z0-9_]*):", SERVICES_YAML.read_text(), re.M))
_USED = re.compile(r"samsungtv_smart\.([a-z_][a-z0-9_]*)")


def _used_services():
    """(service, file, line) for every samsungtv_smart.* reference in examples."""
    for path in sorted(EXAMPLES.rglob("*.yaml")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for name in _USED.findall(line):
                yield name, path.relative_to(ROOT), lineno


def test_services_yaml_defines_the_expected_core_services():
    # Guard the guard: if parsing broke, _DEFINED would be empty/garbage.
    assert {"art_set_slideshow", "art_get_current", "art_set_artmode"} <= _DEFINED


def test_every_example_service_exists():
    unknown = [
        f"{service} ({path}:{lineno})"
        for service, path, lineno in _used_services()
        if service not in _DEFINED
    ]
    assert (
        not unknown
    ), "services used in examples but not in services.yaml:\n" + "\n".join(unknown)
