"""Contract-test main-owned CodeScene coverage publication."""

from __future__ import annotations

import typing as typ
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
MAIN_PATH = ROOT / ".github" / "workflows" / "coverage-main.yml"
SHARED_ACTIONS_REVISION = "152d9c4784d0ae5877938a984fe6d1f04d718fd8"


def _workflow(path: Path) -> dict[typ.Any, typ.Any]:
    """Load a workflow mapping from ``path``."""
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path.name} must contain a mapping"
    return typ.cast("dict[typ.Any, typ.Any]", workflow)


def _job(workflow: dict[typ.Any, typ.Any], name: str) -> dict[typ.Any, typ.Any]:
    """Return the named workflow job."""
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow must declare a jobs mapping"
    job = jobs.get(name)
    assert isinstance(job, dict), f"jobs.{name} must be a mapping"
    return typ.cast("dict[typ.Any, typ.Any]", job)


def _step(job: dict[typ.Any, typ.Any], name: str) -> dict[typ.Any, typ.Any]:
    """Return the uniquely named step from a workflow job."""
    steps = job.get("steps")
    assert isinstance(steps, list), "job must declare steps"
    matches = [
        step for step in steps if isinstance(step, dict) and step.get("name") == name
    ]
    assert len(matches) == 1, f"expected one step named {name!r}, got {len(matches)}"
    return typ.cast("dict[typ.Any, typ.Any]", matches[0])


def test_pull_requests_keep_coverage_local() -> None:
    """Pull-request CI ratchets coverage without CodeScene access."""
    lint_test = _job(_workflow(CI_PATH), "lint-test")
    checkout = _step(lint_test, "Check out repository")
    coverage = _step(lint_test, "Generate coverage")
    assert "fetch-depth" not in checkout.get("with", {})
    assert coverage["uses"] == (
        "leynos/shared-actions/.github/actions/generate-coverage@"
        f"{SHARED_ACTIONS_REVISION}"
    )
    assert coverage["with"] == {
        "language": "python",
        "python-source": "pkg",
        "output-path": "coverage.xml",
        "format": "cobertura",
        "pytest-workers": "",
        "with-ratchet": "true",
    }
    assert "CS_ACCESS_TOKEN" not in lint_test.get("env", {})
    assert not any(
        isinstance(step, dict)
        and "upload-codescene-coverage" in str(step.get("uses", ""))
        for step in lint_test["steps"]
    )


def test_main_push_owns_codescene_publication() -> None:
    """Main coverage uploads the same ratcheted report to CodeScene."""
    workflow = _workflow(MAIN_PATH)
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"push": {"branches": ["main"]}}
    upload = _step(_job(workflow, "coverage-upload"), "Upload coverage to CodeScene")
    assert upload["uses"] == (
        "leynos/shared-actions/.github/actions/upload-codescene-coverage@"
        f"{SHARED_ACTIONS_REVISION}"
    )
    assert upload["with"]["mode"] == "upload"
