"""Pylint wrapper that installs the PyPy Astroid shim before linting."""

from __future__ import annotations

import logging
import sys

from ._patch import install_patch

LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Run Pylint after installing the PyPy Astroid object-build patch."""
    install_patch(LOGGER)

    from pylint.lint import Run

    args = sys.argv[1:] if argv is None else argv
    try:
        result = Run(args, exit=False)
    except TypeError as error:
        LOGGER.debug("Falling back to legacy pylint.lint.Run API: %s", error)
        try:
            Run(args)
        except SystemExit as error:
            return _system_exit_code_to_status(error.code)
        return 0
    return int(getattr(result.linter, "msg_status", 0))


def _system_exit_code_to_status(code: object) -> int:
    """Convert legacy Pylint SystemExit payloads into a process status."""
    if code is None:
        LOGGER.debug("Pylint exited without an explicit status")
        return 0
    if isinstance(code, int):
        LOGGER.debug("Pylint exited with status %s", code)
        return code
    LOGGER.warning("Pylint exited with non-integer status %r; returning 1", code)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
