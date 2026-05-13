"""Test doubles and orchestration helpers for patch helper tests."""

from __future__ import annotations

import builtins
import dataclasses
import typing as typ

import pytest

from pylint_pypy_shim import _patch


class FakeClassDef:
    """Minimal Astroid ClassDef stand-in."""


class FakeModule:
    """Minimal Astroid Module stand-in."""


class FakeNodeNG:
    """Minimal Astroid NodeNG stand-in."""


class FakeConst(FakeNodeNG):
    """Minimal Astroid Const stand-in."""

    def __init__(self, value: object) -> None:
        """Store the const value."""
        self.value = value


class FakeNode(_patch._AstroidNode):
    """Minimal Astroid node stand-in for local helper tests."""

    def __init__(self) -> None:
        """Initialise empty local and special-attribute state."""
        self.locals: dict[str, list[object]] = {}
        self.special_attributes: set[str] = set()

    def add_local_node(self, child_node: object, name: str) -> None:
        """Record child attachments like Astroid nodes do."""
        self.locals.setdefault(name, []).append(child_node)


class FakeBuilder(_patch._InspectBuilder):
    """Minimal InspectBuilder stand-in for local helper tests."""

    def __init__(self) -> None:
        """Initialise builder state for one test."""
        self._done: dict[object, object] = {}
        self._module = "test-module"
        self.object_build_calls: list[tuple[object, object]] = []

    def imported_member(
        self,
        node: _patch._AstroidNode,
        member: object,
        alias: str,
    ) -> bool:
        """Treat test members as local unless a test overrides this method."""
        del self
        del node, member, alias
        return False

    def object_build(self, child: object, member: object) -> None:
        """Record recursive object-build calls."""
        self.object_build_calls.append((child, member))


_ORIGINAL_FAKE_BUILDER_OBJECT_BUILD = FakeBuilder.object_build


@pytest.fixture(autouse=True)
def reset_fake_astroid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset patched Astroid stand-ins so tests do not depend on order."""
    monkeypatch.setattr(
        FakeBuilder,
        "object_build",
        _ORIGINAL_FAKE_BUILDER_OBJECT_BUILD,
    )

    class FreshFakeBuilder(FakeBuilder):
        """Fresh InspectBuilder stand-in for per-test monkeypatch isolation."""

    monkeypatch.setattr(_patch.node_classes, "CONST_CLS", (str, int, float, bool))
    monkeypatch.setattr(_patch.nodes, "Module", FakeModule)
    monkeypatch.setattr(_patch.nodes, "ClassDef", FakeClassDef)
    monkeypatch.setattr(_patch.nodes, "NodeNG", FakeNodeNG)
    monkeypatch.setattr(_patch.nodes, "Const", FakeConst)
    monkeypatch.setattr(_patch.nodes, "const_factory", FakeConst)
    monkeypatch.setattr(_patch.raw_building, "InspectBuilder", FreshFakeBuilder)
    monkeypatch.setattr(_patch, "IS_PYPY", False)
    monkeypatch.setattr(_patch, "_PATCH_INSTALLED", False)
    monkeypatch.setattr(
        _patch, "_build_from_function", lambda node, member, mod: object()
    )
    monkeypatch.setattr(_patch, "_safe_has_attribute", hasattr)
    monkeypatch.setattr(_patch, "attach_dummy_node", lambda node, alias: None)
    monkeypatch.setattr(_patch, "build_dummy", lambda member: object())
    monkeypatch.setattr(_patch, "build_module", lambda alias: FakeModule())
    monkeypatch.setattr(
        _patch, "object_build_class", lambda node, member: FakeClassDef()
    )
    monkeypatch.setattr(
        _patch,
        "object_build_datadescriptor",
        lambda node, member: object(),
    )
    monkeypatch.setattr(
        _patch,
        "object_build_methoddescriptor",
        lambda node, member: object(),
    )


class ObjectBuildScenario:
    """Shared objects for object-builder orchestration tests."""

    def __init__(self) -> None:
        """Initialise a fresh routing scenario."""
        self.builder = FakeBuilder()
        self.node = FakeNode()
        self.target = object()
        self.pypy_child = object()
        self.ordinary_child = object()


@dataclasses.dataclass
class RoutingSpies:
    """Spy callables and call logs for object-builder routing tests."""

    fake_dir: typ.Callable[[object], list[object]]
    fake_resolve_member: typ.Callable[
        [object, object, str, object | None],
        tuple[object | None, bool, bool],
    ]
    fake_build_builtin_child: typ.Callable[[object, object, object, str], object]
    fake_dispatch_member_to_child: typ.Callable[[object, object, object, str], object]
    fake_attach_child_node: typ.Callable[[object, str], None]
    builtin_calls: list[str]
    dispatch_calls: list[str]
    attach_calls: list[tuple[object, str]]


def make_routing_spies(scenario: ObjectBuildScenario) -> RoutingSpies:
    """Build spy callables for routing verification."""
    builtin_calls: list[str] = []
    dispatch_calls: list[str] = []
    attach_calls: list[tuple[object, str]] = []

    def fake_dir(obj: object) -> list[object]:
        assert obj is scenario.target, (
            "obj passed to fake_dir must be the target object"
        )
        return ["__class_getitem__", object(), "missing", "ordinary"]

    def fake_resolve_member(
        node_arg: object,
        obj: object,
        alias: str,
        logger: object | None = None,
    ) -> tuple[object | None, bool, bool]:
        del logger
        assert node_arg is scenario.node, "node argument must be forwarded unchanged"
        assert obj is scenario.target, "obj argument must be forwarded unchanged"
        if alias == "missing":
            return None, False, True
        return object(), alias == "__class_getitem__", False

    def fake_build_builtin_child(
        builder_arg: object,
        node_arg: object,
        member: object,
        alias: str,
    ) -> object:
        del member
        assert builder_arg is scenario.builder, (
            "builder argument must be forwarded unchanged"
        )
        assert node_arg is scenario.node, "node argument must be forwarded unchanged"
        assert alias == "__class_getitem__", (
            "_build_builtin_child must only be called for __class_getitem__"
        )
        builtin_calls.append(alias)
        return scenario.pypy_child

    def fake_dispatch_member_to_child(
        builder_arg: object,
        node_arg: object,
        member: object,
        alias: str,
    ) -> object:
        del member
        assert builder_arg is scenario.builder, (
            "builder argument must be forwarded unchanged"
        )
        assert node_arg is scenario.node, "node argument must be forwarded unchanged"
        assert alias == "ordinary", (
            "_dispatch_member_to_child must only be called for ordinary aliases"
        )
        dispatch_calls.append(alias)
        return scenario.ordinary_child

    def fake_attach_child_node(node_arg: object, alias: str) -> None:
        attach_calls.append((node_arg, alias))

    return RoutingSpies(
        fake_dir=fake_dir,
        fake_resolve_member=fake_resolve_member,
        fake_build_builtin_child=fake_build_builtin_child,
        fake_dispatch_member_to_child=fake_dispatch_member_to_child,
        fake_attach_child_node=fake_attach_child_node,
        builtin_calls=builtin_calls,
        dispatch_calls=dispatch_calls,
        attach_calls=attach_calls,
    )


def setup_fake_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    spies: RoutingSpies,
) -> None:
    """Register fake object-builder dependencies."""
    monkeypatch.setattr(builtins, "dir", spies.fake_dir)
    monkeypatch.setattr(_patch, "_resolve_member", spies.fake_resolve_member)
    monkeypatch.setattr(_patch, "_build_builtin_child", spies.fake_build_builtin_child)
    monkeypatch.setattr(
        _patch,
        "_dispatch_member_to_child",
        spies.fake_dispatch_member_to_child,
    )
    monkeypatch.setattr(_patch, "attach_dummy_node", spies.fake_attach_child_node)


def run_object_builder(
    builder: FakeBuilder,
    node: FakeNode,
    target: object,
) -> None:
    """Run the patched object builder with the provided test doubles."""
    _patch._object_build_without_pypy_descriptor_aliases(builder, node, target)


def assert_builder_outcome(
    scenario: ObjectBuildScenario,
    spies: RoutingSpies,
) -> None:
    """Verify child attachment and skipped-alias dummy handling."""
    assert scenario.node.locals == {
        "__class_getitem__": [scenario.pypy_child],
        "ordinary": [scenario.ordinary_child],
    }, "locals must contain exactly the two children from their respective builders"
    assert spies.attach_calls == [(scenario.node, "missing")], (
        "object builder must attach a dummy node for skipped aliases"
    )
