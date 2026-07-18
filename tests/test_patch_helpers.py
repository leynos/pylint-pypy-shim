"""Unit tests for the PyPy-safe Astroid patch helpers.

Kills the ``_build_builtin_child`` untested-path survivors tracked in #24
and the dispatch argument-propagation survivors tracked in #26.
"""

from __future__ import annotations

import builtins
import collections
import concurrent.futures
import inspect
import logging
import re
import sys
import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pylint_pypy_shim import _patch

from .test_patch_support import (
    FakeBuilder,
    FakeClassDef,
    FakeConst,
    FakeNode,
    ObjectBuildScenario,
    as_astroid_node,
    as_inspect_builder,
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


_DISPATCH_LOGGER = logging.getLogger("tests.dispatch")


def test_resolve_member_unwraps_bound_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound methods are unwrapped before Astroid child construction."""
    node = FakeNode()
    monkeypatch.setattr(_patch, "IS_PYPY", False)

    member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
        as_astroid_node(node),
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
    assert _patch.get_metrics() == collections.Counter({
        "resolve.bound_method_unwrapped": 1,
        "resolve.success": 1,
    }), "unwrapping a bound method must record exactly those two counters"


def test_resolve_member_keeps_pypy_class_getitem_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyPy's ``__class_getitem__`` descriptor alias stays bound."""
    node = FakeNode()
    monkeypatch.setattr(_patch, "IS_PYPY", True)

    member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
        as_astroid_node(node),
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
    assert _patch.get_metrics() == collections.Counter({"resolve.success": 1}), (
        "a successful resolve must record exactly the resolve.success counter"
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
        as_astroid_node(node),
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
    assert _patch.get_metrics() == collections.Counter({
        "resolve.getattr_failure": 1
    }), "a getattr failure must record exactly the resolve.getattr_failure counter"


@pytest.mark.parametrize("exception_type", [AttributeError, TypeError])
def test_resolve_member_logs_getattr_failures(
    caplog: pytest.LogCaptureFixture,
    exception_type: type[Exception],
) -> None:
    """Getattr failures are observable at debug level."""

    class FailingAttribute:
        def __getattr__(self, name: str) -> object:
            raise exception_type(name)

    with caplog.at_level(logging.DEBUG, logger=_patch.__name__):
        member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
            as_astroid_node(FakeNode()),
            FailingAttribute(),
            "missing",
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
        as_astroid_node(FakeNode()),
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

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        len,
        "len",
        _DISPATCH_LOGGER,
    )

    assert result is child, (
        "_dispatch_member_to_child must return the value from _build_builtin_child"
    )


def test_dispatch_member_to_child_routes_classes_uncached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Classes are built and recursively inspected when not cached."""
    builder = FakeBuilder()
    node = FakeNode()
    member = type("SampleClass", (), {})
    child = FakeClassDef()
    object_build_class_calls: list[tuple[object, type]] = []

    def fake_object_build_class(node_arg: object, member_arg: type) -> FakeClassDef:
        object_build_class_calls.append((node_arg, member_arg))
        return child

    monkeypatch.setattr(_patch, "object_build_class", fake_object_build_class)

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        member,
        "sample",
        _DISPATCH_LOGGER,
    )

    assert result is child
    assert object_build_class_calls == [(node, member)]
    assert builder.object_build_calls == [(child, member)]


def test_build_class_child_reuses_cached_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached ClassDef is reused without rebuilding the child."""
    builder = FakeBuilder()
    node = FakeNode()
    member = type("CachedClass", (), {})
    cached_child = FakeClassDef()
    builder._done[member] = cached_child

    def fail_object_build_class(node_arg: object, member_arg: object) -> object:
        del node_arg, member_arg
        msg = "object_build_class should not be called on a cache hit"
        raise AssertionError(msg)

    monkeypatch.setattr(_patch, "object_build_class", fail_object_build_class)

    result = _patch._build_class_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        member,
        "cached",
    )

    assert result is cached_child
    assert builder.object_build_calls == []


def test_build_class_child_raises_on_cached_non_class() -> None:
    """A cached non-ClassDef entry triggers the cached-child assertion."""
    builder = FakeBuilder()
    node = FakeNode()
    member = type("BrokenCachedClass", (), {})
    builder._done[member] = object()

    with pytest.raises(_patch.PatchError, match="must be a ClassDef"):
        _patch._build_class_child(
            as_inspect_builder(builder),
            as_astroid_node(node),
            member,
            "broken",
        )


def test_dispatch_member_to_child_routes_method_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Method descriptors are routed through object_build_methoddescriptor."""
    builder = FakeBuilder()
    node = FakeNode()
    child = object()
    calls: list[tuple[object, object]] = []

    def fake_object_build_methoddescriptor(
        node_arg: object,
        member_arg: object,
    ) -> object:
        calls.append((node_arg, member_arg))
        return child

    monkeypatch.setattr(
        _patch,
        "object_build_methoddescriptor",
        fake_object_build_methoddescriptor,
    )
    member = object()
    monkeypatch.setattr(
        _patch.inspect, "ismethoddescriptor", lambda value: value is member
    )

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        member,
        "upper",
        _DISPATCH_LOGGER,
    )

    assert result is child
    assert calls == [(node, member)]
    assert _patch.get_metrics()["dispatch.method_descriptor"] == 1


def test_dispatch_member_to_child_routes_data_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data descriptors are routed through object_build_datadescriptor."""
    builder = FakeBuilder()
    node = FakeNode()
    child = object()
    calls: list[tuple[object, object]] = []

    def fake_object_build_datadescriptor(
        node_arg: object,
        member_arg: object,
    ) -> object:
        calls.append((node_arg, member_arg))
        return child

    monkeypatch.setattr(
        _patch,
        "object_build_datadescriptor",
        fake_object_build_datadescriptor,
    )
    member = property(lambda self: 42)

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        member,
        "answer",
        _DISPATCH_LOGGER,
    )

    assert result is child
    assert calls == [(node, member)]
    assert _patch.get_metrics()["dispatch.data_descriptor"] == 1


def test_build_const_child_respects_special_attributes() -> None:
    """Special attributes skip const creation."""
    node = FakeNode()
    node.special_attributes.add("__special__")

    result = _patch._build_const_child(as_astroid_node(node), 42, "__special__")

    assert result is None


def test_dispatch_member_to_child_routes_constants() -> None:
    """Constants are built through the configured const factory."""
    builder = FakeBuilder()
    node = FakeNode()

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        42,
        "answer",
        _DISPATCH_LOGGER,
    )

    assert isinstance(result, FakeConst)
    assert result.value == 42


def test_dispatch_member_to_child_routes_routines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routines are routed through _build_from_function with the builder module."""
    builder = FakeBuilder()
    node = FakeNode()
    child = object()
    calls: list[tuple[object, object, object]] = []

    def fake_build_from_function(
        node_arg: object,
        member_arg: object,
        module_arg: object,
    ) -> object:
        calls.append((node_arg, member_arg, module_arg))
        return child

    monkeypatch.setattr(_patch, "_build_from_function", fake_build_from_function)

    def member() -> None:
        return None

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        member,
        "member",
        _DISPATCH_LOGGER,
    )

    assert result is child
    assert calls == [(node, member, builder._module)]
    assert _patch.get_metrics()["dispatch.routine"] == 1


def test_dispatch_member_to_child_routes_all_exporting_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Objects with __all__ become modules and are recursively inspected."""
    builder = FakeBuilder()
    node = FakeNode()
    member = type("AllModuleLike", (), {"__all__": []})()
    child = object()
    calls: list[str] = []

    def fake_build_module(alias: str) -> object:
        calls.append(alias)
        return child

    monkeypatch.setattr(_patch, "build_module", fake_build_module)

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        member,
        "module_like",
        _DISPATCH_LOGGER,
    )

    assert result is child
    assert calls == ["module_like"]
    assert builder.object_build_calls == [(child, member)]
    assert _patch.get_metrics()["dispatch.module_like"] == 1


def test_dispatch_member_to_child_uses_dummy_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown member types are represented by a dummy child."""
    builder = FakeBuilder()
    node = FakeNode()
    member = object()
    child = object()
    calls: list[object] = []

    def fake_build_dummy(member_arg: object) -> object:
        calls.append(member_arg)
        return child

    monkeypatch.setattr(_patch, "build_dummy", fake_build_dummy)

    with caplog.at_level(logging.DEBUG):
        result = _patch._dispatch_member_to_child(
            as_inspect_builder(builder),
            as_astroid_node(node),
            member,
            "child",
            _DISPATCH_LOGGER,
        )

    assert result is child
    assert calls == [member]
    assert _patch.get_metrics()["dispatch.dummy"] == 1
    assert "Dispatching child as dummy member" in caplog.text


def test_record_metric_counts_concurrent_increments() -> None:
    """Metric increments remain consistent under concurrent callers."""
    metric_name = "tests.concurrent_metric"
    increment_count = 1_000
    start_value = _patch.get_metrics()[metric_name]

    with concurrent.futures.ThreadPoolExecutor() as executor:
        list(
            executor.map(
                lambda _: _patch._record_metric(metric_name),
                range(increment_count),
            )
        )

    assert _patch.get_metrics()[metric_name] == start_value + increment_count


def test_attach_child_node_avoids_duplicate_locals() -> None:
    """Child attachment preserves Astroid's existing-local guard."""
    node = FakeNode()
    child = object()

    _patch._attach_child_node(
        as_astroid_node(node),
        "child",
        typ.cast("_patch.nodes.NodeNG", child),
    )
    _patch._attach_child_node(
        as_astroid_node(node),
        "child",
        typ.cast("_patch.nodes.NodeNG", child),
    )

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
    target = type("Target", (), {})
    resolved_aliases: list[str] = []

    class _Alias(str):  # ruff:ignore[subclass-builtin] - intentional str-like alias subclass.
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
        lambda builder_arg, node_arg, member, alias, logger=None: object(),
    )

    _patch._object_build_without_pypy_descriptor_aliases(
        as_inspect_builder(builder),
        as_astroid_node(node),
        target,
    )

    assert resolved_aliases == ["child"], (
        "object builder must resolve only string and str-subclass aliases"
    )
    assert list(node.locals) == ["child"], (
        "object builder must not attach locals for non-string aliases"
    )
    assert _patch.get_metrics()["resolve.non_string_dir_entry"] == 2, (
        "each non-string dir entry must increment resolve.non_string_dir_entry"
    )


@given(st.lists(st.one_of(st.integers(), st.none(), st.booleans()), min_size=1))
def test_object_build_ignores_all_non_string_dir_entries_property(
    aliases: list[object],
) -> None:
    """Non-string aliases never reach member resolution or local attachment."""
    builder = FakeBuilder()
    node = FakeNode()
    target = type("Target", (), {})
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
        _patch._object_build_without_pypy_descriptor_aliases(
            as_inspect_builder(builder),
            as_astroid_node(node),
            target,
        )

    assert resolved_aliases == []
    assert node.locals == {}


def test_object_build_returns_early_for_done_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cached targets return before member resolution and local mutation."""
    builder = FakeBuilder()
    node = FakeNode()
    target = type("CachedTarget", (), {})
    sentinel_node = FakeNode()
    builder._done[target] = sentinel_node
    node.locals["preexisting"] = ["keep-me"]

    def fail_dir(obj: object) -> list[str]:
        del obj
        msg = "dir should not be called for cached targets"
        raise AssertionError(msg)

    monkeypatch.setattr(builtins, "dir", fail_dir)

    _patch._object_build_without_pypy_descriptor_aliases(
        as_inspect_builder(builder),
        as_astroid_node(node),
        target,
    )

    assert builder._done[target] is sentinel_node
    assert node.locals == {"preexisting": ["keep-me"]}


def test_object_build_records_done_cache_on_first_visit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-time targets are recorded in the builder cache."""
    builder = FakeBuilder()
    node = FakeNode()
    target = type("UncachedTarget", (), {})

    monkeypatch.setattr(builtins, "dir", lambda obj: [])

    _patch._object_build_without_pypy_descriptor_aliases(
        as_inspect_builder(builder),
        as_astroid_node(node),
        target,
    )

    assert builder._done[target] is node


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


def test_install_patch_skips_non_pypy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The public installer is a no-op outside PyPy."""
    monkeypatch.setattr(_patch.sys.implementation, "name", "cpython", raising=False)

    with caplog.at_level("DEBUG"):
        _patch.install_patch()

    assert "Skipping PyPy Astroid object_build patch on non-PyPy" in caplog.text


def test_install_patch_patches_supported_pypy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Supported PyPy installs the object_build patch."""
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    original_object_build = _patch.raw_building.InspectBuilder.object_build

    with caplog.at_level(logging.INFO):
        _patch.install_patch()

    assert _patch._PATCH_INSTALLED is True
    assert _patch.raw_building.InspectBuilder.object_build is not original_object_build
    assert "Installing PyPy Astroid object_build patch" in caplog.text
    assert "astroid InspectBuilder.object_build patched for PyPy" in caplog.text
    assert re.search(
        r"pylint=\d+\.\d+\.\d+ astroid=\d+\.\d+\.\d+ runtime=pypy",
        caplog.text,
    )
    info_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO
    ]
    assert len(info_messages) == 3
    assert info_messages[0].startswith("Installing PyPy Astroid object_build patch")
    assert info_messages[1] == "astroid InspectBuilder.object_build patched for PyPy"
    assert re.fullmatch(
        r"pylint=\d+\.\d+\.\d+ astroid=\d+\.\d+\.\d+ runtime=pypy",
        info_messages[2],
    )


def test_installed_object_builder_uses_injected_logger(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The installed object builder uses the logger passed to install_patch."""

    class FailingTarget:
        def __dir__(self) -> list[str]:
            return ["existing", "missing"]

        def __getattr__(self, name: str) -> object:
            if name == "existing":
                return object()
            raise AttributeError(name)

    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    logger = logging.getLogger("tests.installed-object-build")
    _patch.install_patch(logger)
    builder_factory = typ.cast("typ.Any", _patch.raw_building.InspectBuilder)
    builder = builder_factory()
    node = FakeNode()

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        builder.object_build(
            as_astroid_node(node), typ.cast("typ.Any", FailingTarget())
        )

    assert "Skipping 'missing'" in caplog.text
    assert "Dispatching existing as dummy member" in caplog.text


def test_installed_object_builder_uses_latest_injected_logger(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeated installs update the runtime logger used by the builder."""

    class Target:
        def __dir__(self) -> list[str]:
            return ["existing"]

        def __getattr__(self, name: str) -> object:
            if name == "existing":
                return object()
            raise AttributeError(name)

    first_logger = logging.getLogger("tests.installed-object-build.first")
    second_logger = logging.getLogger("tests.installed-object-build.second")
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    _patch.install_patch(first_logger)
    _patch.install_patch(second_logger)
    builder_factory = typ.cast("typ.Any", _patch.raw_building.InspectBuilder)
    builder = builder_factory()
    node = FakeNode()

    with caplog.at_level(logging.DEBUG, logger=second_logger.name):
        builder.object_build(as_astroid_node(node), typ.cast("typ.Any", Target()))

    assert "Dispatching existing as dummy member" in caplog.text
    assert all(record.name == second_logger.name for record in caplog.records)


def test_install_patch_is_idempotent_on_supported_pypy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second supported install keeps the existing patched function."""
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    _patch.install_patch()
    first_patched_object_build = _patch.raw_building.InspectBuilder.object_build

    with caplog.at_level(logging.DEBUG):
        _patch.install_patch()

    assert _patch._PATCH_INSTALLED is True
    assert _patch.raw_building.InspectBuilder.object_build is first_patched_object_build
    assert "PyPy Astroid object_build patch already installed" in caplog.text


def test_install_patch_is_idempotent_under_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent installation attempts patch Astroid once."""
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    original_object_build = _patch.raw_building.InspectBuilder.object_build
    original_factory = _patch._object_build_factory
    patched_builders: list[object] = []

    def spy_object_build_factory() -> object:
        patched_builder = original_factory()
        patched_builders.append(patched_builder)
        return patched_builder

    monkeypatch.setattr(_patch, "_object_build_factory", spy_object_build_factory)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        list(executor.map(lambda _: _patch.install_patch(), range(32)))

    assert _patch._PATCH_INSTALLED is True
    assert len(patched_builders) == 1
    assert _patch.raw_building.InspectBuilder.object_build is patched_builders[0]
    assert _patch.raw_building.InspectBuilder.object_build is not original_object_build


def test_install_patch_warns_for_unsupported_versions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unsupported versions warn and leave object_build unchanged."""
    astroid = sys.modules["astroid"]
    pylint = __import__("pylint")
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    monkeypatch.setattr(astroid, "__version__", "0.0.0")
    monkeypatch.setattr(pylint, "__version__", "0.0.0")
    monkeypatch.delenv("PYLINT_PYPY_SHIM_STRICT", raising=False)
    original_object_build = _patch.raw_building.InspectBuilder.object_build

    with caplog.at_level(logging.WARNING):
        _patch.install_patch()

    assert _patch._PATCH_INSTALLED is False
    assert _patch.raw_building.InspectBuilder.object_build is original_object_build
    assert "unsupported versions" in caplog.text


def test_install_patch_treats_non_one_strict_value_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Strict mode is enabled only by the explicit value ``1``."""
    astroid = sys.modules["astroid"]
    pylint = __import__("pylint")
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    monkeypatch.setattr(astroid, "__version__", "0.0.0")
    monkeypatch.setattr(pylint, "__version__", "0.0.0")
    monkeypatch.setenv("PYLINT_PYPY_SHIM_STRICT", "true")
    original_object_build = _patch.raw_building.InspectBuilder.object_build

    with caplog.at_level(logging.WARNING):
        _patch.install_patch()

    assert _patch._PATCH_INSTALLED is False
    assert _patch.raw_building.InspectBuilder.object_build is original_object_build
    assert "unsupported versions" in caplog.text


def test_install_patch_raises_for_unsupported_versions_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package-specific strict mode raises for unsupported versions."""
    astroid = sys.modules["astroid"]
    pylint = __import__("pylint")
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    monkeypatch.setattr(astroid, "__version__", "0.0.0")
    monkeypatch.setattr(pylint, "__version__", "0.0.0")
    monkeypatch.setenv("PYLINT_PYPY_SHIM_STRICT", " 1 ")
    original_object_build = _patch.raw_building.InspectBuilder.object_build

    with pytest.raises(_patch.PatchError, match="unsupported versions"):
        _patch.install_patch()

    assert _patch._PATCH_INSTALLED is False
    assert _patch.raw_building.InspectBuilder.object_build is original_object_build


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("4.0.0", True),
        ("4.9.1", True),
        ("3.9.9", False),
        ("5.0.0", False),
        ("x", False),
    ],
)
def test_is_supported_version_validates_major_range(
    version: str,
    expected: object,
) -> None:
    """Version checks use the supported inclusive/exclusive major range."""
    assert _patch._is_supported_version(version, 4, 5) is expected


def test_validate_astroid_shape_requires_inspect_builder(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Astroid shape validation fails loudly when InspectBuilder is absent."""
    monkeypatch.delattr(_patch.raw_building, "InspectBuilder")

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(_patch.PatchError, match="InspectBuilder"),
    ):
        _patch._validate_astroid_shape()

    assert "InspectBuilder is required" in caplog.text


def test_validate_astroid_shape_requires_callable_object_build(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Astroid shape validation rejects non-callable object_build attributes."""
    monkeypatch.setattr(_patch.raw_building.InspectBuilder, "object_build", None)

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(
            _patch.PatchError,
            match="callable",
        ),
    ):
        _patch._validate_astroid_shape()

    assert "object_build must be callable" in caplog.text


def test_validate_astroid_shape_rejects_unsupported_signature(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Astroid shape validation checks the object_build signature."""

    def object_build(self: object, child: object) -> None:
        del self, child

    monkeypatch.setattr(
        _patch.raw_building.InspectBuilder, "object_build", object_build
    )

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(
            _patch.PatchError,
            match="signature",
        ),
    ):
        _patch._validate_astroid_shape()

    assert "signature is unsupported" in caplog.text


class _RecordingImportedBuilder(FakeBuilder):
    """Builder whose ``imported_member`` records arguments and returns a preset."""

    def __init__(self, *, imported: bool) -> None:
        """Store the canned ``imported_member`` verdict."""
        super().__init__()
        self._imported = imported
        self.imported_member_calls: list[tuple[object, object, str]] = []

    def imported_member(self, node: object, member: object, alias: str) -> bool:
        """Record the exact arguments and return the canned verdict."""
        self.imported_member_calls.append((node, member, alias))
        return self._imported


def test_build_builtin_child_returns_none_for_imported_member() -> None:
    """Imported members yield no child and forward exact arguments."""
    builder = _RecordingImportedBuilder(imported=True)
    node = FakeNode()

    result = _patch._build_builtin_child(
        as_inspect_builder(builder), as_astroid_node(node), len, "len"
    )

    assert result is None
    assert builder.imported_member_calls == [(node, len, "len")]
    assert _patch.get_metrics() == collections.Counter({"dispatch.imported": 1})


def test_build_builtin_child_builds_method_descriptor_for_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-imported builtins build a method descriptor for the exact member."""
    builder = _RecordingImportedBuilder(imported=False)
    node = FakeNode()
    child = object()
    build_calls: list[tuple[object, object]] = []

    def fake_methoddescriptor(node_arg: object, member_arg: object) -> object:
        build_calls.append((node_arg, member_arg))
        return child

    monkeypatch.setattr(_patch, "object_build_methoddescriptor", fake_methoddescriptor)

    result = _patch._build_builtin_child(
        as_inspect_builder(builder), as_astroid_node(node), len, "len"
    )

    assert result is child
    assert builder.imported_member_calls == [(node, len, "len")]
    assert build_calls == [(node, len)]
    assert _patch.get_metrics() == collections.Counter({"dispatch.builtin": 1})


def test_build_class_child_forwards_exact_imported_member_arguments() -> None:
    """`_build_class_child` forwards ``(node, member, alias)`` unchanged."""
    builder = _RecordingImportedBuilder(imported=True)
    node = FakeNode()
    member = type("ImportedSample", (), {})

    result = _patch._build_class_child(
        as_inspect_builder(builder), as_astroid_node(node), member, "sample"
    )

    assert result is None
    assert builder.imported_member_calls == [(node, member, "sample")]
    assert _patch.get_metrics() == collections.Counter({"dispatch.imported": 1})


# Kills the get_metrics() counter survivors tracked in #27.
def test_build_class_child_metrics_distinguish_cached_and_built() -> None:
    """Cache hits and rebuilds increment their own exact counters."""
    builder = FakeBuilder()
    node = FakeNode()
    cached_member = type("CachedSample", (), {})
    built_member = type("BuiltSample", (), {})
    builder._done[cached_member] = FakeClassDef()

    _patch._build_class_child(
        as_inspect_builder(builder), as_astroid_node(node), cached_member, "cached"
    )

    assert _patch.get_metrics() == collections.Counter({"dispatch.class.cached": 1})

    _patch._build_class_child(
        as_inspect_builder(builder), as_astroid_node(node), built_member, "built"
    )

    assert _patch.get_metrics() == collections.Counter({
        "dispatch.class.cached": 1,
        "dispatch.class.built": 1,
    })


def test_build_class_child_error_names_member_and_cached_type() -> None:
    """The cached-child error names the member and the actual cached type."""
    builder = FakeBuilder()
    node = FakeNode()
    member = type("BrokenCachedSample", (), {})
    builder._done[member] = object()

    with pytest.raises(_patch.PatchError) as excinfo:
        _patch._build_class_child(
            as_inspect_builder(builder), as_astroid_node(node), member, "broken"
        )

    assert str(excinfo.value) == (
        f"_done entry for {member!r} must be a ClassDef, got object"
    )


def test_build_const_child_metrics_distinguish_special_and_const() -> None:
    """Special-attribute skips and const builds increment exact counters."""
    node = FakeNode()
    node.special_attributes.add("__special__")

    assert _patch._build_const_child(as_astroid_node(node), 1, "__special__") is None
    assert _patch.get_metrics() == collections.Counter({"dispatch.const.special": 1})

    result = _patch._build_const_child(as_astroid_node(node), 2, "plain")

    assert isinstance(result, FakeConst)
    assert _patch.get_metrics() == collections.Counter({
        "dispatch.const.special": 1,
        "dispatch.const": 1,
    })


def test_dispatch_member_to_child_forwards_alias_to_class_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Class dispatch forwards the alias to `_build_class_child` unchanged."""
    builder = FakeBuilder()
    node = FakeNode()
    member = type("AliasSample", (), {})
    child = FakeClassDef()
    calls: list[tuple[object, object, object, object]] = []

    def fake_build_class_child(
        builder_arg: object,
        node_arg: object,
        member_arg: object,
        alias_arg: object,
    ) -> object:
        calls.append((builder_arg, node_arg, member_arg, alias_arg))
        return child

    monkeypatch.setattr(_patch, "_build_class_child", fake_build_class_child)

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        member,
        "sample",
        _DISPATCH_LOGGER,
    )

    assert result is child
    assert calls == [(builder, node, member, "sample")]


def test_dispatch_member_to_child_respects_const_special_alias() -> None:
    """Const dispatch passes the alias through to the special-attribute gate."""
    builder = FakeBuilder()
    node = FakeNode()
    node.special_attributes.add("__doc__")

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        "docstring",
        "__doc__",
        _DISPATCH_LOGGER,
    )

    assert result is None
    assert _patch.get_metrics() == collections.Counter({"dispatch.const.special": 1})


def test_resolve_member_ignores_class_getitem_on_non_pypy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`__class_getitem__` is only special when running on PyPy."""
    monkeypatch.setattr(_patch, "IS_PYPY", False)

    member, is_pypy_class_getitem, should_skip = _patch._resolve_member(
        as_astroid_node(FakeNode()),
        _ClassWithClassGetItem,
        "__class_getitem__",
    )

    assert is_pypy_class_getitem is False, (
        "__class_getitem__ must not be flagged when IS_PYPY is False"
    )
    assert should_skip is False
    assert member is _ClassWithClassGetItem.__dict__["__class_getitem__"].__func__, (
        "off PyPy the bound method must be unwrapped like any other"
    )


# Kills the dispatch.dummy.getattr_failure counter survivor tracked in #27.
def test_object_build_records_getattr_failure_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipped members record the dummy getattr-failure counter."""
    builder = FakeBuilder()
    node = FakeNode()
    target = type("SkippingTarget", (), {})
    attach_calls: list[tuple[object, str]] = []

    monkeypatch.setattr(builtins, "dir", lambda obj: ["missing"])
    monkeypatch.setattr(
        _patch,
        "_resolve_member",
        lambda node_arg, obj, alias, logger=None: (None, False, True),
    )
    monkeypatch.setattr(
        _patch,
        "attach_dummy_node",
        lambda node_arg, alias: attach_calls.append((node_arg, alias)),
    )

    _patch._object_build_without_pypy_descriptor_aliases(
        as_inspect_builder(builder), as_astroid_node(node), target
    )

    assert attach_calls == [(node, "missing")]
    assert _patch.get_metrics()["dispatch.dummy.getattr_failure"] == 1


_SKIP_TEMPLATE = (
    "Skipping PyPy Astroid object_build patch for unsupported versions "
    "(pylint {pylint}, astroid {astroid})"
)


# Kills the install_patch version-gate and shape-validation survivors
# tracked in #28.
class _VersionGateCase(typ.NamedTuple):
    """One unsupported-version scenario for the install_patch gate."""

    pylint_version: str | None
    astroid_version: str | None
    reported: tuple[str, str]


def _set_pypy_versions(
    monkeypatch: pytest.MonkeyPatch,
    pylint_version: str | None,
    astroid_version: str | None,
) -> None:
    """Simulate PyPy with the given dependency versions (None removes them)."""
    astroid = sys.modules["astroid"]
    pylint = __import__("pylint")
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    monkeypatch.delenv("PYLINT_PYPY_SHIM_STRICT", raising=False)
    for module, version in ((pylint, pylint_version), (astroid, astroid_version)):
        if version is None:
            monkeypatch.delattr(module, "__version__", raising=False)
        else:
            monkeypatch.setattr(module, "__version__", version)


@pytest.mark.parametrize(
    "case",
    [
        _VersionGateCase("5.0.0", "4.0.0", ("5.0.0", "4.0.0")),
        _VersionGateCase("4.0.0", "5.0.0", ("4.0.0", "5.0.0")),
        _VersionGateCase(None, None, ("0", "0")),
    ],
)
def test_install_patch_version_gate_reports_exact_versions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: _VersionGateCase,
) -> None:
    """One unsupported or missing version skips the patch with exact detail."""
    _set_pypy_versions(monkeypatch, case.pylint_version, case.astroid_version)
    original_object_build = _patch.raw_building.InspectBuilder.object_build

    with caplog.at_level(logging.WARNING):
        _patch.install_patch()

    assert _patch._PATCH_INSTALLED is False
    assert _patch.raw_building.InspectBuilder.object_build is original_object_build
    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert warning_messages == [
        _SKIP_TEMPLATE.format(pylint=case.reported[0], astroid=case.reported[1])
    ]


def test_install_patch_strict_error_message_names_versions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Strict mode raises and logs the exact unsupported-version message."""
    _set_pypy_versions(monkeypatch, "3.0.0", "9.9.9")
    monkeypatch.setenv("PYLINT_PYPY_SHIM_STRICT", "1")
    expected = _SKIP_TEMPLATE.format(pylint="3.0.0", astroid="9.9.9")

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(_patch.PatchError) as excinfo,
    ):
        _patch.install_patch()

    assert str(excinfo.value) == expected
    error_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert error_messages == [expected]


def test_install_patch_reports_shape_errors_on_supplied_logger(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Shape validation inside `install_patch` uses the caller's logger."""
    monkeypatch.setattr(_patch.sys.implementation, "name", "pypy", raising=False)
    monkeypatch.setattr(_patch.raw_building.InspectBuilder, "object_build", None)
    logger = logging.getLogger("tests.install-shape-logger")

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(_patch.PatchError, match="callable"),
    ):
        _patch.install_patch(logger)

    error_records = [
        record for record in caplog.records if record.levelno == logging.ERROR
    ]
    assert [record.name for record in error_records] == [logger.name]


def test_validate_astroid_shape_missing_builder_message(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The missing-builder failure logs its exact message on the given logger."""
    logger = logging.getLogger("tests.shape.missing-builder")
    monkeypatch.delattr(_patch.raw_building, "InspectBuilder")

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(_patch.PatchError) as excinfo,
    ):
        _patch._validate_astroid_shape(logger)

    assert str(excinfo.value) == "astroid.raw_building.InspectBuilder is required"
    error_records = [
        record for record in caplog.records if record.levelno == logging.ERROR
    ]
    assert [record.name for record in error_records] == [logger.name]
    assert error_records[0].getMessage() == (
        "astroid.raw_building.InspectBuilder is required"
    )


def test_validate_astroid_shape_non_callable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-callable failure carries its exact message."""
    monkeypatch.setattr(_patch.raw_building.InspectBuilder, "object_build", None)

    with pytest.raises(_patch.PatchError) as excinfo:
        _patch._validate_astroid_shape()

    assert str(excinfo.value) == "InspectBuilder.object_build must be callable"


def test_validate_astroid_shape_missing_object_build_raises_patch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A builder without ``object_build`` fails as PatchError, not AttributeError."""

    class BuilderWithoutObjectBuild:
        """InspectBuilder stand-in lacking object_build entirely."""

    monkeypatch.setattr(
        _patch.raw_building, "InspectBuilder", BuilderWithoutObjectBuild
    )

    with pytest.raises(_patch.PatchError, match="callable"):
        _patch._validate_astroid_shape()


def test_validate_astroid_shape_signature_error_names_signature(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The signature failure logs the message together with the signature."""

    def object_build(self: object, child: object) -> None:
        del self, child

    monkeypatch.setattr(
        _patch.raw_building.InspectBuilder, "object_build", object_build
    )
    expected_signature = inspect.signature(object_build)

    with (
        caplog.at_level(logging.ERROR),
        pytest.raises(_patch.PatchError) as excinfo,
    ):
        _patch._validate_astroid_shape()

    assert str(excinfo.value) == "InspectBuilder.object_build signature is unsupported"
    error_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert error_messages == [
        f"InspectBuilder.object_build signature is unsupported: {expected_signature}"
    ]


def test_validate_astroid_shape_accepts_extra_trailing_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the first three parameters are constrained; extras are tolerated."""

    def object_build(
        self: object, node: object, obj: object, extra: object = None
    ) -> None:
        del self, node, obj, extra

    monkeypatch.setattr(
        _patch.raw_building.InspectBuilder, "object_build", object_build
    )

    _patch._validate_astroid_shape()


@pytest.mark.parametrize(
    ("is_pypy_class_getitem", "spy_target", "alias"),
    [
        (True, "_build_builtin_child", "__class_getitem__"),
        (False, "_dispatch_member_to_child", "member"),
    ],
)
def test_object_build_forwards_resolved_member(
    monkeypatch: pytest.MonkeyPatch,
    is_pypy_class_getitem: bool,  # ruff:ignore[boolean-type-hint-positional-argument] - parametrized flag, not an API.
    spy_target: str,
    alias: str,
) -> None:
    """Both builder routes receive the resolved member, not None."""
    builder = FakeBuilder()
    node = FakeNode()
    target = type("ForwardingTarget", (), {})
    sentinel = object()
    calls: list[tuple[object, ...]] = []

    def spy(*args: object) -> object:
        calls.append(args[:4])
        return object()

    monkeypatch.setattr(builtins, "dir", lambda obj: [alias])
    monkeypatch.setattr(
        _patch,
        "_resolve_member",
        lambda node_arg, obj, alias_arg, logger=None: (
            sentinel,
            is_pypy_class_getitem,
            False,
        ),
    )
    monkeypatch.setattr(_patch, spy_target, spy)

    _patch._object_build_without_pypy_descriptor_aliases(
        as_inspect_builder(builder), as_astroid_node(node), target
    )

    assert calls == [(builder, node, sentinel, alias)]


def test_dispatch_member_to_child_builds_real_builtin_child() -> None:
    """A genuine builtin member takes the builtin branch and yields a child."""
    builder = FakeBuilder()
    node = FakeNode()

    result = _patch._dispatch_member_to_child(
        as_inspect_builder(builder),
        as_astroid_node(node),
        len,
        "len",
        _DISPATCH_LOGGER,
    )

    assert result is not None
    assert _patch.get_metrics() == collections.Counter({"dispatch.builtin": 1})
