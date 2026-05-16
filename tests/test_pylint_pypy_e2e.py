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

_WRAPPER_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "pylint_pypy.py"


@pytest.mark.timeout(60)
def test_pylint_pypy_wrapper_lints_innocuous_file() -> None:
    """Run the wrapper as a subprocess against a minimal Python file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = pathlib.Path(temp_dir) / "trivial_module.py"
        temp_path.write_text(
            textwrap.dedent("""\
                \"\"\"Trivial module for wrapper smoke testing.\"\"\"

                VALUE = 1
                """),
            encoding="utf-8",
        )

        proc = subprocess.run(  # noqa: S603
            [sys.executable, os.fspath(_WRAPPER_PATH), os.fspath(temp_path)],
            capture_output=True,
            text=True,
            check=False,
        )

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"


@pytest.mark.timeout(60)
def test_pylint_pypy_wrapper_exposes_source_tree_package() -> None:
    """Run the wrapper subprocess and verify it imports the source package."""
    command = textwrap.dedent(
        f"""\
        import runpy
        import sys

        sys.argv = [{os.fspath(_WRAPPER_PATH)!r}, "--version"]
        try:
            runpy.run_path({os.fspath(_WRAPPER_PATH)!r}, run_name="__main__")
        except SystemExit as error:
            if error.code not in (0, None):
                raise

        import pylint_pypy_shim

        print(pylint_pypy_shim.__file__)
        """
    )
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", command],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\n\nstderr:\n{proc.stderr}"
    package_path = pathlib.Path(proc.stdout.strip().splitlines()[-1])
    normalised_path = pathlib.PurePosixPath(package_path.as_posix())
    assert "pkg/pylint_pypy_shim" in normalised_path.as_posix()
