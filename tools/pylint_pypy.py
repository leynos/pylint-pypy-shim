"""Compatibility script for running the PyPy Pylint shim from source."""

from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG_ROOT = _REPO_ROOT / "pkg"


def _run_entrypoint() -> int:
    """Bootstrap ``pkg/`` onto ``sys.path`` and delegate to ``cli.main``."""
    if str(_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(_PKG_ROOT))

    from pylint_pypy_shim.cli import main

    return main()


def main_entrypoint() -> int:
    """Run the PyPy Pylint shim command-line interface."""
    return _run_entrypoint()


if __name__ == "__main__":
    raise SystemExit(main_entrypoint())
