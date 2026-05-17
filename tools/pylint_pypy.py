"""Compatibility script for running the PyPy Pylint shim from source."""

from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG_ROOT = _REPO_ROOT / "pkg"
_PYTHON_COMMAND_ARG_COUNT = 3
_PYTHON_COMMAND_OPTION = "-c"
_PYTHON_COMMAND_SOURCE_INDEX = 2


def _run_entrypoint() -> int:
    """Prepend ``pkg/`` and delegate to ``pylint_pypy_shim.cli.main``."""
    if str(_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(_PKG_ROOT))
    if (
        len(sys.argv) >= _PYTHON_COMMAND_ARG_COUNT
        and sys.argv[1] == _PYTHON_COMMAND_OPTION
    ):
        exec(  # noqa: S102
            sys.argv[_PYTHON_COMMAND_SOURCE_INDEX],
            {"__name__": "__main__"},
        )
        return 0

    from pylint_pypy_shim.cli import main

    return main()


def main_entrypoint() -> int:
    """Run the PyPy Pylint shim command-line interface."""
    return _run_entrypoint()


if __name__ == "__main__":
    raise SystemExit(main_entrypoint())
