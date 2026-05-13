"""Unit tests for the Pylint CLI wrapper."""

from __future__ import annotations

import sys
import types
import typing as typ

from pylint_pypy_shim import cli

if typ.TYPE_CHECKING:
    import pytest


def test_main_installs_patch_and_returns_pylint_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` installs the patch and returns Pylint's message status."""
    calls: list[object] = []

    class FakeLinter:
        msg_status = 4

    class FakeRun:
        def __init__(self, argv: list[str], *, exit: bool) -> None:  # noqa: A002
            calls.append((argv, exit))
            self.linter = FakeLinter()

    fake_pylint = types.ModuleType("pylint")
    fake_lint = typ.cast("typ.Any", types.ModuleType("pylint.lint"))
    fake_lint.Run = FakeRun

    def fake_install_patch(logger: object) -> None:
        calls.append(logger)

    monkeypatch.setattr(cli, "install_patch", fake_install_patch)
    monkeypatch.setitem(sys.modules, "pylint", fake_pylint)
    monkeypatch.setitem(sys.modules, "pylint.lint", fake_lint)

    result = cli.main(["target.py"])

    assert result == 4
    assert calls[1] == (["target.py"], False)
