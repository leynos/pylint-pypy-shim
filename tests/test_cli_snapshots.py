"""Snapshot tests for user-facing CLI output."""

from __future__ import annotations

import subprocess  # noqa: S404 - safe: test harness runs CLI wrapper subprocess.
import sys
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


def test_cli_violation_output_snapshot(
    tmp_path: Path,
    snapshot: SnapshotAssertion,
) -> None:
    """Capture Pylint's full diagnostic output for a deterministic violation."""
    module_path = tmp_path / "violating_module.py"
    module_path.write_text("VALUE = 1\n", encoding="utf-8")

    result = subprocess.run(  # noqa: S603 - safe: controlled input; check=False.
        [
            sys.executable,
            "-m",
            "pylint_pypy_shim.cli",
            "--score=n",
            str(module_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    output = (result.stdout + result.stderr).replace(str(module_path), "<module>")

    assert result.returncode != 0
    assert "missing-module-docstring" in output
    assert output == snapshot
