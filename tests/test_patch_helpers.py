"""Unit tests for the PyPy-safe Astroid patch helpers."""

from __future__ import annotations

import builtins
import inspect
import logging
import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pylint_pypy_shim import _patch

from .test_patch_support import (
    FakeBuilder,
    FakeNode,
    ObjectBuildScenario,
    assert_builder_outcome,
    make_routing_spies,
    run_object_builder,
    setup_fake_dependencies,
)


class _ClassWithClassMethod:
    @classmethod
    def member(cls) -> None:
        """Member used to verify bound-method unwrapping."""


class _ClassWithClassGetItem:
    @classmethod
    def __class_getitem__(cls, item: object) -> object:
        """Member used to verify the PyPy class-getitem exception."""
        return item


def test_resolve_member_unwraps_bound_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound methods are unwrapped before Astroid child construction."""
    node = FakeNode()
    monkeypatch.setattr(_patch, "IS_PYPY", False)

    member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
        node,
        _ClassWithClassMethod,
        "member",
    )

    assert member is _ClassWithClassMethod.__dict__["member"].__func__, (
        "bound method must be unwrapped to its underlying __func__"
    )
    assert is_pypy_class_getitem is False, (
        "alias 'member' must not be flagged as pypy__class_getitem__"
    )
    assert should_skip is False, (
        "_resolve_member must not signal skip for a resolvable alias"
    )


def test_resolve_member_keeps_pypy_class_getitem_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyPy's ``__class_getitem__`` descriptor alias stays bound."""
    node = FakeNode()
    monkeypatch.setattr(_patch, "IS_PYPY", True)

    member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
        node,
        _ClassWithClassGetItem,
        "__class_getitem__",
    )

    assert inspect.ismethod(member), (
        "__class_getitem__ must remain a bound method when IS_PYPY is True"
    )
    assert (
        member.__func__ is _ClassWithClassGetItem.__dict__["__class_getitem__"].__func__
    ), "__func__ must point to the underlying classmethod function"
    assert is_pypy_class_getitem is True, (
        "alias '__class_getitem__' must be flagged when IS_PYPY is True"
    )
    assert should_skip is False, (
        "_resolve_member must not signal skip for resolvable __class_getitem__"
    )


def test_resolve_member_signals_skip_when_getattr_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Getattr failures return a skip signal without side effects."""
    node = FakeNode()
    calls: list[tuple[object, str]] = []

    def fake_attach_dummy_node(target_node: object, alias: str) -> None:
        calls.append((target_node, alias))

    monkeypatch.setattr(_patch, "attach_dummy_node", fake_attach_dummy_node)

    member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
        node,
        _ClassWithClassMethod,
        "missing",
    )

    assert member is None, (
        "_resolve_member must return None as member when getattr fails"
    )
    assert is_pypy_class_getitem is False, (
        "alias 'missing' must not be flagged as pypy__class_getitem__"
    )
    assert should_skip is True, "_resolve_member must signal skip when getattr raises"
    assert not calls, "_resolve_member must not attach dummy nodes directly"


@pytest.mark.parametrize("exception_type", [AttributeError, TypeError])
def test_resolve_member_logs_getattr_failures(
    caplog: pytest.LogCaptureFixture,
    exception_type: type[Exception],
) -> None:
    """Getattr failures are observable at debug level."""

    class FailingAttribute:
        def __getattr__(self, name: str) -> object:
            raise exception_type(name)

    logger = logging.getLogger("tests.resolve-member")
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
            FakeNode(),
            FailingAttribute(),
            "missing",
            logger,
        )

    assert member is None
    assert is_pypy_class_getitem is False
    assert should_skip is True
    assert "Skipping 'missing'" in caplog.text


@given(st.sampled_from([AttributeError, TypeError]))
def test_resolve_member_getattr_failure_property(
    exception_type: type[Exception],
) -> None:
    """Supported getattr failures always return a skip signal."""

    class FailingAttribute:
        def __getattr__(self, name: str) -> object:
            raise exception_type(name)

    member, _is_pypy_class_getitem, should_skip = _patch._resolve_member(
        FakeNode(),
        FailingAttribute(),
        "missing",
    )

    assert member is None
    assert should_skip is True


def test_dispatch_member_to_child_routes_builtins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builtins are routed through the dedicated builtin builder."""
    builder = FakeBuilder()
    node = FakeNode()
    child = object()

    def fake_build_builtin_child(
        builder_arg: object,
        node_arg: object,
        member_arg: object,
        alias_arg: str,
    ) -> object:
        assert builder_arg is builder, "builder argument must be forwarded unchanged"
        assert node_arg is node, "node argument must be forwarded unchanged"
        assert member_arg is len, "member argument must be forwarded unchanged"
        assert alias_arg == "len", "alias argument must be forwarded unchanged"
        return child

    monkeypatch.setattr(_patch, "_build_builtin_child", fake_build_builtin_child)

    result = _patch._dispatch_member_to_child(builder, node, len, "len")

    assert result is child, (
        "_dispatch_member_to_child must return the value from _build_builtin_child"
    )


def test_attach_child_node_avoids_duplicate_locals() -> None:
    """Child attachment preserves Astroid's existing-local guard."""
    node = FakeNode()
    child = object()

    _patch._attach_child_node(node, "child", child)
    _patch._attach_child_node(node, "child", child)

    assert node.locals["child"] == [child], (
        "_attach_child_node must not duplicate an already-attached child"
    )


def test_object_build_routes_pypy_class_getitem_at_call_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The object builder keeps PyPy class-getitem handling outside dispatch."""
    scenario = ObjectBuildScenario()
    spies = make_routing_spies(scenario)
    setup_fake_dependencies(monkeypatch, spies)

    run_object_builder(scenario.builder, scenario.node, scenario.target)

    assert_builder_outcome(scenario, spies)


def test_object_build_ignores_non_string_dir_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-string ``dir()`` entries are ignored before member resolution."""
    builder = FakeBuilder()
    node = FakeNode()
    target = object()
    resolved_aliases: list[str] = []

    class _Alias(str):  # noqa: FURB189
        """String subclass used to prove str-like aliases are accepted."""

    def fake_dir(obj: object) -> list[object]:
        assert obj is target, "obj passed to fake_dir must be the target object"
        return [object(), 123, _Alias("child")]

    def fake_resolve_member(
        node_arg: object,
        obj: object,
        alias: str,
        logger: object | None = None,
    ) -> tuple[object, bool, bool]:
        del logger
        assert node_arg is node, "node argument must be forwarded unchanged"
        assert obj is target, "obj argument must be forwarded unchanged"
        resolved_aliases.append(alias)
        return object(), False, False

    monkeypatch.setattr(builtins, "dir", fake_dir)
    monkeypatch.setattr(_patch, "_resolve_member", fake_resolve_member)
    monkeypatch.setattr(
        _patch,
        "_dispatch_member_to_child",
        lambda builder_arg, node_arg, member, alias: object(),
    )

    _patch._object_build_without_pypy_descriptor_aliases(builder, node, target)

    assert resolved_aliases == ["child"], (
        "object builder must resolve only string and str-subclass aliases"
    )
    assert list(node.locals) == ["child"], (
        "object builder must not attach locals for non-string aliases"
    )


@given(st.lists(st.one_of(st.integers(), st.none(), st.booleans()), min_size=1))
def test_object_build_ignores_all_non_string_dir_entries_property(
    aliases: list[object],
) -> None:
    """Non-string aliases never reach member resolution or local attachment."""
    builder = FakeBuilder()
    node = FakeNode()
    target = object()
    resolved_aliases: list[str] = []

    def fake_dir(obj: object) -> list[object]:
        assert obj is target
        return aliases

    def fake_resolve_member(
        node_arg: object,
        obj: object,
        alias: str,
        logger: object | None = None,
    ) -> tuple[object, bool, bool]:
        del node_arg, obj, logger
        resolved_aliases.append(alias)
        return object(), False, False

    with pytest.MonkeyPatch.context() as property_monkeypatch:
        property_monkeypatch.setattr(builtins, "dir", fake_dir)
        property_monkeypatch.setattr(_patch, "_resolve_member", fake_resolve_member)
        _patch._object_build_without_pypy_descriptor_aliases(builder, node, target)

    assert resolved_aliases == []
    assert node.locals == {}


def test_fake_builder_object_build_resets_between_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A class-level monkey patch cannot leak into later tests."""

    def patched_object_build(self: object, child: object, member: object) -> None:
        del self, child, member
        msg = "leaked patch"
        raise AssertionError(msg)

    monkeypatch.setattr(FakeBuilder, "object_build", patched_object_build)

    builder = FakeBuilder()

    with pytest.raises(AssertionError, match="leaked patch"):
        builder.object_build(object(), object())


def test_fake_builder_object_build_is_original_after_reset() -> None:
    """The autouse reset fixture restores FakeBuilder.object_build."""
    builder = FakeBuilder()
    child = object()
    member = object()

    builder.object_build(child, member)

    assert builder.object_build_calls == [(child, member)]


def test_protocol_stub_methods_raise_not_implemented() -> None:
    """Protocol helper stubs fail loudly if called directly."""
    with pytest.raises(NotImplementedError):
        _patch._AstroidNode.add_local_node(
            typ.cast("_patch._AstroidNode", object()), object(), "child"
        )
    with pytest.raises(NotImplementedError):
        _patch._InspectBuilder.imported_member(
            typ.cast("_patch._InspectBuilder", object()),
            FakeNode(),
            object(),
            "child",
        )
    with pytest.raises(NotImplementedError):
        _patch._InspectBuilder.object_build(
            typ.cast("_patch._InspectBuilder", object()),
            object(),
            object(),
        )


def test_install_patch_skips_non_pypy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The public installer is a no-op outside PyPy."""
    monkeypatch.setattr(_patch.sys.implementation, "name", "cpython", raising=False)

    with caplog.at_level("DEBUG"):
        _patch.install_patch()

    assert "Skipping PyPy Astroid object_build patch on non-PyPy" in caplog.text
