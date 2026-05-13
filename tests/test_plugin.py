"""Unit tests for the Pylint plugin entry point."""

from __future__ import annotations

import logging
import typing as typ

import pylint_pypy_shim
from pylint_pypy_shim import plugin

if typ.TYPE_CHECKING:
    import pytest


def test_register_installs_patch_without_using_linter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plugin entry point only installs the shim patch."""
    calls: list[logging.Logger] = []
    linter = object()

    def fake_install_patch(logger: logging.Logger) -> None:
        calls.append(logger)

    monkeypatch.setattr(plugin, "install_patch", fake_install_patch)

    plugin.register(linter)

    assert calls == [logging.getLogger(plugin.__name__)]


def test_package_register_matches_documented_plugin_path() -> None:
    """The documented Pylint plugin module exposes ``register`` directly."""
    assert pylint_pypy_shim.register is plugin.register
    assert "register" in pylint_pypy_shim.__all__
