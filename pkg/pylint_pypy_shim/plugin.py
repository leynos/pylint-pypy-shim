"""Pylint plugin entry point for the PyPy Astroid shim."""

from __future__ import annotations

import logging

from ._patch import install_patch


def register(linter: object) -> None:
    """Install the Astroid patch without changing Pylint configuration."""
    del linter
    install_patch(logging.getLogger(__name__))
