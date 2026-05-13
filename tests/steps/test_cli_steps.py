"""Behaviour steps for the optional CLI wrapper."""

from __future__ import annotations

import subprocess  # noqa: S404
import sys
import typing as typ

import pytest
from pytest_bdd import given, scenario, then, when

if typ.TYPE_CHECKING:
    from pathlib import Path


@scenario(
    "../features/cli.feature", "Linting a trivial module through the CLI succeeds"
)
def test_cli_lints_trivial_module() -> None:
    """Run the CLI feature."""


@given("a trivial Python module", target_fixture="module_path")
def given_trivial_python_module(tmp_path: Path) -> Path:
    """Create a lint-clean Python module."""
    module_path = tmp_path / "trivial_module.py"
    module_path.write_text(
        '"""Trivial module for CLI smoke testing."""\n\nVALUE = 1\n',
        encoding="utf-8",
    )
    return module_path


@when("I run the pylint-pypy shim CLI", target_fixture="cli_result")
def when_run_cli(module_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the wrapper in a subprocess."""
    pytest.importorskip("pylint")
    return subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pylint_pypy_shim.cli",
            str(module_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


@then("the CLI exits successfully")
def then_cli_exits_successfully(
    cli_result: subprocess.CompletedProcess[str],
) -> None:
    """Assert the wrapper propagated Pylint success."""
    assert cli_result.returncode == 0, cli_result.stdout + cli_result.stderr
