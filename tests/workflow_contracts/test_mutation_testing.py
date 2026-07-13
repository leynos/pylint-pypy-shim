"""Contract tests for the mutation-testing caller workflow.

The executable logic lives in the ``leynos/shared-actions`` reusable
workflow, which carries its own unit and integration tests; this
repository's caller is declarative configuration. These tests parse the
caller with PyYAML and pin the contract it must uphold, so drift
(repointing the pin at a branch, widening permissions, or losing the
mutmut configuration) fails CI on the pull request rather than
surfacing in a scheduled or manual run.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "mutation-testing.yml"
)

pytestmark = pytest.mark.skipif(
    not WORKFLOW_PATH.exists(),
    reason="workflow file not present in this working copy (e.g. "
    "inside mutmut's mutants/ sandbox, which does not copy .github/)",
)

#: The leynos/shared-actions commit providing mutation-mutmut.yml.
#: Bump the caller workflow and this constant together. This is also the
#: estate-wide floor pin for the CodeScene coverage rollout
#: (shared-actions#334); the repo keeps a single pin across workflows.
PINNED_SHA = "927edd45ae77be4251a8a18ca9eb5613a2e32cbd"

EXPECTED_USES = (
    "leynos/shared-actions/.github/workflows/mutation-mutmut.yml@" + PINNED_SHA
)

EXPECTED_WITH = {
    "paths": "pkg/",
    "module-prefix-strip": "pkg/",
}

EXPECTED_CRON = "5 12 * * *"


def _load() -> dict[typ.Any, typ.Any]:
    """Parse the workflow file."""
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "the workflow must parse to a mapping"
    return typ.cast("dict[typ.Any, typ.Any]", workflow)


def _triggers(workflow: dict[typ.Any, typ.Any]) -> dict[typ.Any, typ.Any]:
    """Return the ``on:`` mapping (PyYAML parses the bare key as True)."""
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), "the workflow must declare an on: mapping"
    return typ.cast("dict[typ.Any, typ.Any]", triggers)


def _mutation_job(workflow: dict[typ.Any, typ.Any]) -> dict[typ.Any, typ.Any]:
    """Return the single calling job."""
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "the workflow must declare a jobs mapping"
    assert list(jobs) == ["mutation"], (
        f"expected a single job named 'mutation', found {sorted(jobs)}"
    )
    job = jobs["mutation"]
    assert isinstance(job, dict), "jobs.mutation must be a mapping"
    return typ.cast("dict[typ.Any, typ.Any]", job)


def test_uses_reference_is_pinned_to_the_documented_sha() -> None:
    """The job must call the shared workflow at the exact documented SHA."""
    uses = _mutation_job(_load()).get("uses")
    assert uses is not None, "jobs.mutation.uses is missing"
    assert isinstance(uses, str), f"jobs.mutation.uses must be a string, got {uses!r}"
    path, _, ref = uses.partition("@")
    assert path == "leynos/shared-actions/.github/workflows/mutation-mutmut.yml", (
        f"jobs.mutation.uses must reference mutation-mutmut.yml, got {path!r}"
    )
    assert len(ref) == 40, (
        f"jobs.mutation.uses must pin a full 40-character commit SHA, "
        f"not a branch or tag: {ref!r}"
    )
    assert all(c in "0123456789abcdef" for c in ref), (
        f"jobs.mutation.uses must pin a lowercase hex commit SHA, "
        f"not a branch or tag: {ref!r}"
    )
    assert uses == EXPECTED_USES, (
        f"jobs.mutation.uses pins {ref!r}; this test documents {PINNED_SHA!r} — "
        "bump the workflow and this test together"
    )


def test_job_permissions_are_exactly_least_privilege() -> None:
    """The job grants contents: read and id-token: write, nothing broader."""
    permissions = _mutation_job(_load()).get("permissions")
    assert permissions == {"contents": "read", "id-token": "write"}, (
        "jobs.mutation.permissions must be exactly "
        f"{{'contents': 'read', 'id-token': 'write'}}, got {permissions!r}"
    )


def test_workflow_default_permissions_are_empty() -> None:
    """The workflow-level default token scope is empty."""
    workflow = _load()
    assert workflow.get("permissions") == {}, (
        f"top-level permissions must be an empty mapping, got "
        f"{workflow.get('permissions')!r}"
    )


def test_concurrency_serializes_per_ref_without_cancelling() -> None:
    """Runs queue per ref instead of cancelling one another."""
    concurrency = _load().get("concurrency")
    assert isinstance(concurrency, dict), "the workflow must declare concurrency"
    assert concurrency.get("group") == "mutation-testing-${{ github.ref }}", (
        f"concurrency.group must key on the triggering ref, got "
        f"{concurrency.get('group')!r}"
    )
    assert concurrency.get("cancel-in-progress") is False, (
        f"concurrency.cancel-in-progress must be false, got "
        f"{concurrency.get('cancel-in-progress')!r}"
    )


def test_triggers_keep_schedule_and_plain_dispatch() -> None:
    """The daily schedule stays; dispatch declares no inputs."""
    triggers = _triggers(_load())
    schedule = triggers.get("schedule")
    assert schedule == [{"cron": EXPECTED_CRON}], (
        f"on.schedule must be [{{'cron': {EXPECTED_CRON!r}}}], got {schedule!r}"
    )
    assert "workflow_dispatch" in triggers, "on.workflow_dispatch is missing"
    dispatch = triggers.get("workflow_dispatch") or {}
    assert isinstance(dispatch, dict), (
        f"on.workflow_dispatch must be a mapping or empty, got {dispatch!r}"
    )
    assert not dispatch.get("inputs"), (
        "on.workflow_dispatch must not declare inputs; the Actions "
        "run-workflow control selects the ref"
    )


def test_with_block_carries_the_caller_configuration() -> None:
    """The caller passes exactly the documented mutmut configuration."""
    with_block = _mutation_job(_load()).get("with")
    assert with_block == EXPECTED_WITH, (
        f"jobs.mutation.with must equal {EXPECTED_WITH!r}, got {with_block!r}"
    )
