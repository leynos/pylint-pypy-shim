"""Unit tests for the PyPy-safe Astroid patch helpers."""

from __future__ import annotations

import builtins
import inspect
import logging
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
            as_astroid_node(FakeNode()),
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
    )

    assert result is child
    assert calls == [(node, member)]


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
    )

    assert result is child
    assert calls == [(node, member)]


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
    )

    assert result is child
    assert calls == [(node, member, builder._module)]


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
    )

    assert result is child
    assert calls == ["module_like"]
    assert builder.object_build_calls == [(child, member)]


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
        )

    assert result is child
    assert calls == [member]
    assert _patch.get_metrics()["dispatch.dummy"] == 1
    assert "Dispatching child as dummy member" in caplog.text


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

    class _Alias(str):  # noqa: FURB189 - intentional str-like alias subclass.
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
