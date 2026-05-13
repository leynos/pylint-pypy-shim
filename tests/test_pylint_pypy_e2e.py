"""Subprocess coverage for the PyPy Pylint wrapper."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess  # noqa: S404
import sys
import tempfile
import textwrap

import pytest

if sys.implementation.name != "pypy" or importlib.util.find_spec("pylint") is None:
    pytest.skip("requires PyPy and pylint", allow_module_level=True)


@pytest.mark.timeout(60)
def test_pylint_pypy_wrapper_lints_innocuous_file() -> None:
    """Run the wrapper as a subprocess against a minimal Python file."""
    temp_dir = tempfile.TemporaryDirectory()
    temp_path = pathlib.Path(temp_dir.name) / "trivial_module.py"
    temp_path.write_text(
        textwrap.dedent("""\
            \"\"\"Trivial module for wrapper smoke testing.\"\"\"

            VALUE = 1
            """),
        encoding="utf-8",
    )

    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "tools/pylint_pypy.py", os.fspath(temp_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        temp_dir.cleanup()

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
