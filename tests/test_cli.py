"""Unit tests for the Pylint CLI wrapper.

Kills the CLI exit-status and argument-handling survivors tracked in #25.
"""

from __future__ import annotations

import logging
import sys
import types
import typing as typ

import pytest

from pylint_pypy_shim import _patch, cli, plugin


def _install_fake_pylint(
    monkeypatch: pytest.MonkeyPatch,
    run_cls: type,
    *,
    implementation: str = "cpython",
) -> None:
    """Install fake ``pylint``/``pylint.lint`` modules exposing *run_cls*."""
    fake_pylint = typ.cast("typ.Any", types.ModuleType("pylint"))
    fake_pylint.__version__ = "4.0.0"
    fake_pylint.__path__ = []
    fake_lint = typ.cast("typ.Any", types.ModuleType("pylint.lint"))
    fake_lint.Run = run_cls
    fake_pylint.lint = fake_lint
    monkeypatch.setattr(
        _patch.sys.implementation, "name", implementation, raising=False
    )
    monkeypatch.setitem(sys.modules, "pylint", fake_pylint)
    monkeypatch.setitem(sys.modules, "pylint.lint", fake_lint)


def _make_modern_run(calls: list[tuple[list[str], bool]], linter: object) -> type:
    """Build a modern ``Run`` stand-in recording ``(argv, exit)`` calls."""

    class FakeRun:
        def __init__(self, argv: list[str], *, exit: bool) -> None:  # ruff:ignore[builtin-argument-shadowing]
            calls.append((argv, exit))
            self.linter = linter

    return FakeRun


def test_main_defaults_to_sys_argv_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without explicit argv, `main` forwards exactly ``sys.argv[1:]`` to Run."""
    calls: list[tuple[list[str], bool]] = []
    _install_fake_pylint(
        monkeypatch, _make_modern_run(calls, types.SimpleNamespace(msg_status=0))
    )
    monkeypatch.setattr(sys, "argv", ["pylint-pypy", "first.py", "second.py"])

    assert cli.main() == 0
    assert calls == [(["first.py", "second.py"], False)], (
        "main(None) must forward sys.argv[1:] unchanged"
    )


def test_main_passes_module_logger_to_install_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` wires the CLI module logger into `install_patch`."""
    loggers: list[object] = []
    _install_fake_pylint(
        monkeypatch, _make_modern_run([], types.SimpleNamespace(msg_status=0))
    )
    monkeypatch.setattr(cli, "install_patch", loggers.append)

    assert cli.main(["target.py"]) == 0
    assert loggers == [cli.LOGGER], (
        "install_patch must receive the pylint_pypy_shim.cli logger"
    )


def test_main_returns_zero_when_linter_lacks_msg_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A linter without ``msg_status`` yields status 0, not an error."""
    _install_fake_pylint(monkeypatch, _make_modern_run([], object()))

    assert cli.main(["target.py"]) == 0


def test_main_legacy_run_receives_args_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy Run that returns normally yields 0 and gets the parsed args."""
    calls: list[list[str]] = []

    class LegacyRun:
        def __init__(self, argv: list[str]) -> None:
            calls.append(argv)

    _install_fake_pylint(monkeypatch, LegacyRun)

    assert cli.main(["target.py"]) == 0
    assert calls == [["target.py"]], (
        "the legacy Run API must receive the parsed args, not None"
    )


def test_system_exit_non_integer_status_warning_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The non-integer-status warning names the payload and the fallback."""
    with caplog.at_level(logging.WARNING, logger=cli.LOGGER.name):
        status = cli._system_exit_code_to_status("fatal")

    assert status == 0
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert warnings == ["Pylint exited with non-integer status 'fatal'; returning 0"]


def test_main_installs_patch_and_returns_pylint_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main` installs the real patch and returns Pylint's message status."""
    calls: list[tuple[list[str], bool]] = []
    _install_fake_pylint(
        monkeypatch,
        _make_modern_run(calls, types.SimpleNamespace(msg_status=4)),
        implementation="pypy",
    )
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

    _install_fake_pylint(monkeypatch, FakeRun)

    assert cli.main(["target.py"]) == expected_status


def test_plugin_and_cli_share_idempotent_patch(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Plugin and CLI installation in one process do not reassign the patch."""
    calls: list[tuple[list[str], bool]] = []
    _install_fake_pylint(
        monkeypatch,
        _make_modern_run(calls, types.SimpleNamespace(msg_status=0)),
        implementation="pypy",
    )

    plugin.register(object())
    patched_object_build = _patch.raw_building.InspectBuilder.object_build

    with caplog.at_level("DEBUG"):
        status = cli.main(["target.py"])

    assert status == 0
    assert calls == [(["target.py"], False)]
    assert _patch.raw_building.InspectBuilder.object_build is patched_object_build
    assert "PyPy Astroid object_build patch already installed" in caplog.text
