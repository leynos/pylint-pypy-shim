"""Unit tests for the Pylint CLI wrapper."""

from __future__ import annotations

import sys
import types
import typing as typ

import pytest

from pylint_pypy_shim import _patch, cli, plugin


def test_main_installs_patch_and_returns_pylint_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` installs the real patch and returns Pylint's message status."""
    calls: list[object] = []

    class FakeLinter:
        msg_status = 4

    class FakeRun:
        def __init__(self, argv: list[str], *, exit: bool) -> None:  # noqa: A002
            calls.append((argv, exit))
            self.linter = FakeLinter()

    fake_pylint = typ.cast("typ.Any", types.ModuleType("pylint"))
    fake_pylint.__version__ = "4.0.0"
    fake_pylint.__path__ = []
    fake_lint = typ.cast("typ.Any", types.ModuleType("pylint.lint"))
    fake_lint.Run = FakeRun
    fake_pylint.lint = fake_lint

    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    monkeypatch.setitem(sys.modules, "pylint", fake_pylint)
    monkeypatch.setitem(sys.modules, "pylint.lint", fake_lint)
    original_object_build = _patch.raw_building.InspectBuilder.object_build

    result = cli.main(["target.py"])

    assert result == 4
    assert calls == [(["target.py"], False)]
    assert _patch._PATCH_INSTALLED is True
    assert _patch.raw_building.InspectBuilder.object_build is not original_object_build


@pytest.mark.parametrize(
    ("system_exit_code", "expected_status"),
    [(None, 0), (3, 3), (True, 1), (False, 0), ("fatal", 0)],
)
def test_main_handles_legacy_pylint_system_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    system_exit_code: object,
    expected_status: int,
) -> None:
    """Legacy Pylint API exit payloads are converted safely."""

    class FakeRun:
        def __init__(self, argv: list[str]) -> None:
            del argv
            raise SystemExit(system_exit_code)

    fake_pylint = typ.cast("typ.Any", types.ModuleType("pylint"))
    fake_lint = typ.cast("typ.Any", types.ModuleType("pylint.lint"))
    fake_lint.Run = FakeRun
    fake_pylint.lint = fake_lint

    monkeypatch.setattr(_patch.sys.implementation, "name", "cpython", raising=False)
    monkeypatch.setitem(sys.modules, "pylint", fake_pylint)
    monkeypatch.setitem(sys.modules, "pylint.lint", fake_lint)

    assert cli.main(["target.py"]) == expected_status


def test_plugin_and_cli_share_idempotent_patch(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Plugin and CLI installation in one process do not reassign the patch."""
    calls: list[tuple[list[str], bool]] = []

    class FakeLinter:
        msg_status = 0

    class FakeRun:
        def __init__(self, argv: list[str], *, exit: bool) -> None:  # noqa: A002
            calls.append((argv, exit))
            self.linter = FakeLinter()

    fake_pylint = typ.cast("typ.Any", types.ModuleType("pylint"))
    fake_pylint.__version__ = "4.0.0"
    fake_pylint.__path__ = []
    fake_lint = typ.cast("typ.Any", types.ModuleType("pylint.lint"))
    fake_lint.Run = FakeRun
    fake_pylint.lint = fake_lint

    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    monkeypatch.setitem(sys.modules, "pylint", fake_pylint)
    monkeypatch.setitem(sys.modules, "pylint.lint", fake_lint)

    plugin.register(object())
    patched_object_build = _patch.raw_building.InspectBuilder.object_build

    with caplog.at_level("DEBUG"):
        status = cli.main(["target.py"])

    assert status == 0
    assert calls == [(["target.py"], False)]
    assert _patch.raw_building.InspectBuilder.object_build is patched_object_build
    assert "PyPy Astroid object_build patch already installed" in caplog.text
