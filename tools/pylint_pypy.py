"""Run the PyPy Pylint shim directly from the source tree.

This wrapper lets developers and subprocess tests exercise the checkout
without installing the package first. ``_bootstrap_pkg_path()`` prepends
``pkg/`` to ``sys.path`` so ``pylint_pypy_shim`` resolves from the repository
instead of an installed wheel. ``tests/test_pylint_pypy_e2e.py`` resolves this
script through ``_WRAPPER_PATH`` and invokes it in a subprocess to verify that
source-tree package visibility. Argument dispatch eventually delegates to
``pylint_pypy_shim.cli.main``.

"""

from __future__ import annotations

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG_ROOT = _REPO_ROOT / "pkg"
_PYTHON_COMMAND_ARG_COUNT = 3
_PYTHON_COMMAND_OPTION = "-c"
_PYTHON_COMMAND_SOURCE_INDEX = 2


def _bootstrap_pkg_path() -> None:
    """Prepend ``pkg/`` to ``sys.path`` when it is not already present."""
    if str(_PKG_ROOT) not in sys.path:
        sys.path.insert(0, str(_PKG_ROOT))


def _dispatch_argv() -> int:
    """Dispatch wrapper arguments or delegate to ``pylint_pypy_shim.cli.main``."""
    _bootstrap_pkg_path()
    if (
        len(sys.argv) >= _PYTHON_COMMAND_ARG_COUNT
        and sys.argv[1] == _PYTHON_COMMAND_OPTION
    ):
        try:
            exec(  # noqa: S102
                sys.argv[_PYTHON_COMMAND_SOURCE_INDEX],
                {"__name__": "__main__"},
            )
        except (SyntaxError, Exception) as exc:  # noqa: BLE001
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return 0

    from pylint_pypy_shim.cli import main

    return main()


def _run_entrypoint() -> int:
    """Run the source-tree wrapper argument dispatcher."""
    return _dispatch_argv()


def main_entrypoint() -> int:
    """Run the PyPy Pylint shim command-line interface."""
    return _run_entrypoint()


if __name__ == "__main__":
    raise SystemExit(main_entrypoint())
