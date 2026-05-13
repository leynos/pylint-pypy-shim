"""Compatibility script for running the PyPy Pylint shim from source."""

from __future__ import annotations

from pylint_pypy_shim.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
