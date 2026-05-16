"""Property-based tests for object-build filtering behaviour."""

from __future__ import annotations

import builtins
import typing as typ

import pytest

pytest.importorskip("hypothesis")
from hypothesis import given
from hypothesis import strategies as st

from pylint_pypy_shim import _patch

from .test_patch_support import ObjectBuildScenario, run_object_builder


@given(
    non_string_aliases=st.lists(
        st.one_of(st.integers(), st.floats(), st.binary(), st.none()),
        min_size=0,
        max_size=5,
    ),
    include_string_alias=st.booleans(),
)
def test_non_string_dir_entries_are_ignored(
    non_string_aliases: list[object],
    include_string_alias: object,
) -> None:
    """Only string aliases from ``dir()`` reach local attachment."""
    scenario = ObjectBuildScenario()
    has_string_alias = bool(include_string_alias)

    def fake_dir(obj: object) -> list[object]:
        assert obj is scenario.target
        aliases: list[object] = [*non_string_aliases]
        if has_string_alias:
            aliases.append("ordinary")
        return aliases

    def fake_resolve_member(
        node_arg: object,
        obj: object,
        alias: str,
        logger: object | None = None,
    ) -> tuple[object, bool, bool]:
        del logger
        assert node_arg is scenario.node
        assert obj is scenario.target
        assert alias == "ordinary"
        return object(), False, False

    def fake_dispatch_member_to_child(
        builder_arg: object,
        node_arg: object,
        member: object,
        alias: str,
        logger: object | None = None,
    ) -> object:
        del member, logger
        assert builder_arg is scenario.builder
        assert node_arg is scenario.node
        assert alias == "ordinary"
        return scenario.ordinary_child

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(builtins, "dir", fake_dir)
        monkeypatch.setattr(_patch, "_resolve_member", fake_resolve_member)
        monkeypatch.setattr(
            _patch,
            "_dispatch_member_to_child",
            fake_dispatch_member_to_child,
        )

        run_object_builder(scenario.builder, scenario.node, scenario.target)

    if has_string_alias:
        assert scenario.node.locals == {"ordinary": [scenario.ordinary_child]}
    else:
        assert scenario.node.locals == {}


@given(st.sampled_from([AttributeError, TypeError]))
def test_getattr_failures_signal_skip(
    error_type: type[AttributeError] | type[TypeError],
) -> None:
    """Skipped aliases from getattr failures receive one dummy attachment."""
    scenario = ObjectBuildScenario()
    attach_calls: list[tuple[object, str]] = []

    def fake_dir(obj: object) -> list[str]:
        assert obj is scenario.target
        return ["missing"]

    def fake_resolve_member(
        node_arg: object,
        obj: object,
        alias: str,
        logger: object | None = None,
    ) -> tuple[None, bool, bool]:
        del logger
        assert node_arg is scenario.node
        assert obj is scenario.target
        assert alias == "missing"
        assert issubclass(error_type, _patch._IGNORED_GETATTR_ERRORS)
        return None, False, True

    def fake_attach_dummy_node(node_arg: object, alias: str) -> None:
        attach_calls.append((node_arg, alias))

    def fail_dispatch_member_to_child(
        builder_arg: object,
        node_arg: object,
        member: object,
        alias: str,
        logger: object | None = None,
    ) -> typ.NoReturn:
        del builder_arg, node_arg, member, alias, logger
        msg = "skipped getattr failures must not dispatch members"
        raise AssertionError(msg)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(builtins, "dir", fake_dir)
        monkeypatch.setattr(_patch, "_resolve_member", fake_resolve_member)
        monkeypatch.setattr(_patch, "attach_dummy_node", fake_attach_dummy_node)
        monkeypatch.setattr(
            _patch,
            "_dispatch_member_to_child",
            fail_dispatch_member_to_child,
        )

        run_object_builder(scenario.builder, scenario.node, scenario.target)

    assert attach_calls == [(scenario.node, "missing")]
