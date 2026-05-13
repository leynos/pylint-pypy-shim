"""PyPy compatibility shim for Pylint's Astroid inspection builder."""

from __future__ import annotations

from ._version import __version__
from .plugin import register

__all__ = ["__version__", "register"]
