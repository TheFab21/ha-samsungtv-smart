"""The one-button release workflow (.github/workflows/publish.yaml).

The button exists so a release does not wait on hand-cut tags. The one thing
that is easy to get wrong, and easy to "simplify" back into being wrong later:
a release created with the default ``GITHUB_TOKEN`` does NOT trigger other
workflows, so publish.yaml cannot create the release and leave the zip to
release.yml — release.yml would never fire and the HACS asset would be missing.
publish.yaml therefore builds and attaches the zip itself.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"
PUBLISH = WORKFLOWS / "publish.yaml"
RELEASE = WORKFLOWS / "release.yml"
MANIFEST = "custom_components/samsungtv_smart/manifest.json"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def _on(workflow: dict) -> dict:
    # "on:" is parsed as the boolean True by YAML 1.1.
    return workflow.get("on", workflow.get(True))


def test_the_button_exists_and_is_dispatchable():
    workflow = _load(PUBLISH)
    triggers = _on(workflow)
    assert "workflow_dispatch" in triggers
    inputs = triggers["workflow_dispatch"]["inputs"]
    # Pressing it with nothing filled in must be the normal path.
    for name in ("version", "notes", "prerelease"):
        assert inputs[name].get("required") is not True, name


def test_the_button_attaches_the_zip_itself():
    # The GITHUB_TOKEN subtlety above: it must not rely on release.yml firing.
    steps = _steps(_load(PUBLISH), "publish")
    zipped = [s for s in steps if "zip samsungtv_smart.zip" in (s.get("run") or "")]
    assert zipped, "publish.yaml must build the HACS zip"
    creates = [s for s in steps if "action-gh-release" in (s.get("uses") or "")]
    assert len(creates) == 1
    assert "samsungtv_smart.zip" in creates[0]["with"]["files"]
    assert creates[0]["with"]["generate_release_notes"] is True


def test_the_button_releases_master_not_the_branch_it_was_started_from():
    steps = _steps(_load(PUBLISH), "publish")
    checkout = next(s for s in steps if "actions/checkout" in (s.get("uses") or ""))
    assert checkout["with"]["ref"] == "refs/heads/master"
    # Tags are needed to see that a version is already out.
    assert checkout["with"]["fetch-depth"] == 0


def test_the_button_refuses_a_version_that_is_already_released():
    script = "\n".join(
        s.get("run") or "" for s in _steps(_load(PUBLISH), "publish")
    )
    assert "refs/tags/$version" in script
    assert "already released" in script
    # And it takes the version from the manifest the PR bumped.
    assert "jq -r '.version'" in script


def test_it_needs_write_access_and_cannot_race_itself():
    workflow = _load(PUBLISH)
    assert workflow["permissions"]["contents"] == "write"
    assert workflow["concurrency"]["group"] == "publish-release"
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_the_existing_release_workflow_is_left_alone():
    # A release cut by hand in the GitHub UI still has a person as the actor,
    # so release.yml fires for it and remains the path for that case.
    triggers = _on(_load(RELEASE))
    assert triggers["release"]["types"] == ["published"]
