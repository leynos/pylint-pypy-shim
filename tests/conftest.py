"""Shared pytest fixtures for shim tests."""

from __future__ import annotations

import pytest

from pylint_pypy_shim import _patch

from .test_patch_support import (
    _ORIGINAL_FAKE_BUILDER_OBJECT_BUILD,
    FakeBuilder,
    FakeClassDef,
    FakeConst,
    FakeModule,
    FakeNodeNG,
)


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
