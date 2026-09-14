"""Pylint wrapper that installs the PyPy Astroid shim before linting."""

from __future__ import annotations

import inspect
import logging
import sys
import typing as typ

from ._patch import install_patch

if typ.TYPE_CHECKING:
    import collections.abc as cabc

LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Run Pylint after installing the PyPy Astroid object-build patch."""
    install_patch(LOGGER)

    from pylint.lint import Run

    args = sys.argv[1:] if argv is None else argv
    try:
        if _run_supports_exit_parameter(Run):
            result = Run(args, exit=False)
            return int(getattr(result.linter, "msg_status", 0))
        # Diagnostic log lines carry no behavioural contract (issue #29).
        # pragma: no mutate start
        LOGGER.debug("Using legacy pylint.lint.Run API without exit parameter")
        # pragma: no mutate end
        Run(args)
    except SystemExit as error:
        return _system_exit_code_to_status(error.code)
    return 0


def _run_supports_exit_parameter(run: cabc.Callable[..., object]) -> bool:
    """Return whether the installed Pylint ``Run`` accepts ``exit=``."""
    return "exit" in inspect.signature(run).parameters


def _system_exit_code_to_status(code: object) -> int:
    """Convert legacy Pylint SystemExit payloads into a process status."""
    if code is None:
        LOGGER.debug("Pylint exited without an explicit status")  # pragma: no mutate
        return 0
    if isinstance(code, int):
        LOGGER.debug("Pylint exited with status %s", code)  # pragma: no mutate
        return code
    LOGGER.warning("Pylint exited with non-integer status %r; returning 0", code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
