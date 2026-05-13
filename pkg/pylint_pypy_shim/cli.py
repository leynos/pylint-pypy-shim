"""Pylint wrapper that installs the PyPy Astroid shim before linting."""

from __future__ import annotations

import logging
import sys

from ._patch import install_patch


def main(argv: list[str] | None = None) -> int:
    """Run Pylint after installing the PyPy Astroid object-build patch."""
    install_patch(logging.getLogger(__name__))

    from pylint.lint import Run

    args = sys.argv[1:] if argv is None else argv
    try:
        result = Run(args, exit=False)
    except TypeError:
        try:
            Run(args)
        except SystemExit as error:
            return int(error.code or 0)
        return 0
    return int(getattr(result.linter, "msg_status", 0))


if __name__ == "__main__":
    raise SystemExit(main())
