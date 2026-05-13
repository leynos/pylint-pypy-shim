"""PyPy-safe Astroid object builder patch.

PyPy exposes a few descriptor aliases differently from CPython. Astroid's
inspection builder can then try to resolve non-string ``dir()`` entries or
descriptor aliases in ways that fail before Pylint can attach a harmless dummy
node.

``install_patch()`` applies a guarded monkey patch to Astroid's
``InspectBuilder.object_build`` implementation so descriptor and attribute
resolution behave correctly under PyPy:

```python
from pylint_pypy_shim._patch import install_patch

install_patch()
```

Invoke it early in process startup, before Pylint begins importing modules for
analysis. The patch is a no-op on CPython and is limited to supported Pylint and
Astroid major versions.
"""

from __future__ import annotations

import inspect
import logging
import os
import sys
import threading
import typing as typ
import warnings

from astroid import node_classes, nodes, raw_building
from astroid.raw_building import (
    IS_PYPY,
    _build_from_function,
    _safe_has_attribute,
    attach_dummy_node,
    build_dummy,
    build_module,
    object_build_class,
    object_build_datadescriptor,
    object_build_methoddescriptor,
)

if typ.TYPE_CHECKING:
    import types

_IGNORED_GETATTR_ERRORS = (AttributeError, TypeError)
_STRICT_ENV_VAR = "PYLINT_PYPY_SHIM_STRICT"
_PATCH_LOCK = threading.Lock()
_PATCH_INSTALLED = False


class PatchError(RuntimeError):
    """Base exception for PyPy shim patch failures."""


class CachedChildTypeError(PatchError):
    """Raised when Astroid's builder cache contains an unexpected node type."""


def _cached_child_type_error(member: object, child: object) -> str:
    """Describe a cached Astroid child type invariant failure."""
    return f"_done entry for {member!r} must be a ClassDef, got {type(child).__name__}"


def _build_builtin_child(
    self: raw_building.InspectBuilder,
    node: nodes.Module | nodes.ClassDef,
    member: object,
    alias: str,
) -> nodes.NodeNG | None:
    """Build a child for builtins unless Astroid treats it as imported."""
    if self.imported_member(node, member, alias):
        return None
    return object_build_methoddescriptor(
        node,
        member,  # type: ignore[invalid-argument-type]
    )


def _build_class_child(
    self: raw_building.InspectBuilder,
    node: nodes.Module | nodes.ClassDef,
    member: type,
    alias: str,
) -> nodes.ClassDef | None:
    """Build or reuse a class child unless Astroid treats it as imported."""
    if self.imported_member(node, member, alias):
        return None
    if member in self._done:
        child = self._done[member]
        if not isinstance(child, nodes.ClassDef):
            raise CachedChildTypeError(_cached_child_type_error(member, child))
        return child
    child = object_build_class(node, member)
    self.object_build(child, member)
    return child


def _build_const_child(
    node: nodes.Module | nodes.ClassDef,
    member: object,
    alias: str,
) -> nodes.NodeNG | None:
    """Build a const child unless the alias is already a special attribute."""
    if alias in node.special_attributes:
        return None
    return nodes.const_factory(member)  # type: ignore[invalid-return-type]


def _attach_child_node(
    node: nodes.Module | nodes.ClassDef,
    alias: str,
    child: nodes.NodeNG,
) -> None:
    """Attach *child* to *node* under *alias* unless it is already present."""
    if child not in node.locals.get(alias, ()):
        node.add_local_node(child, alias)


def _dispatch_member_to_child(  # noqa: PLR0911 -- dispatcher exits by member type.
    self: raw_building.InspectBuilder,
    node: nodes.Module | nodes.ClassDef,
    member: object,
    alias: str,
) -> nodes.NodeNG | None:
    """Dispatch members to the matching Astroid builder."""
    if inspect.isbuiltin(member):
        return _build_builtin_child(self, node, member, alias)
    if inspect.isclass(member):
        return _build_class_child(self, node, member, alias)
    if inspect.ismethoddescriptor(member):
        return object_build_methoddescriptor(
            node,
            member,
        )
    if inspect.isdatadescriptor(member):
        return object_build_datadescriptor(
            node,
            member,  # type: ignore[invalid-argument-type]
        )
    if isinstance(member, tuple(node_classes.CONST_CLS)):
        return _build_const_child(node, member, alias)
    if inspect.isroutine(member):
        return _build_from_function(
            node,
            member,
            self._module,  # type: ignore[invalid-argument-type]
        )
    if _safe_has_attribute(member, "__all__"):
        child = build_module(alias)
        self.object_build(child, member)  # type: ignore[invalid-argument-type]
        return child
    return build_dummy(member)


def _resolve_member(
    _node: nodes.Module | nodes.ClassDef,
    obj: object,
    alias: str,
    logger: logging.Logger | None = None,
) -> tuple[object | None, bool, bool]:
    """Resolve *alias* from *obj* and report whether the caller should skip it."""
    pypy__class_getitem__ = IS_PYPY and alias == "__class_getitem__"
    try:
        member = getattr(obj, alias)
    except _IGNORED_GETATTR_ERRORS as error:
        if logger is not None:
            logger.debug("Skipping %r from %r: %s", alias, obj, error)
        return None, pypy__class_getitem__, True
    if inspect.ismethod(member) and not pypy__class_getitem__:
        member = member.__func__
    return member, pypy__class_getitem__, False


def _object_build_without_pypy_descriptor_aliases(
    self: raw_building.InspectBuilder,
    node: nodes.Module | nodes.ClassDef,
    obj: types.ModuleType | type,
) -> None:
    """Build Astroid nodes while ignoring non-string PyPy ``dir()`` entries."""
    if obj in self._done:
        return
    self._done[obj] = node
    logger = logging.getLogger(__name__)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for alias in dir(obj):
            if not isinstance(alias, str):
                continue
            member, pypy__class_getitem__, skip = _resolve_member(
                node, obj, alias, logger
            )
            if skip:
                attach_dummy_node(node, alias)
                continue
            if pypy__class_getitem__:
                child = _build_builtin_child(self, node, member, alias)
            else:
                child = _dispatch_member_to_child(self, node, member, alias)
            if child is not None:
                _attach_child_node(node, alias, child)


def install_patch(logger: logging.Logger | None = None) -> None:
    """Install the PyPy Astroid object-build patch when versions are supported."""
    active_logger = logger or logging.getLogger(__name__)
    if sys.implementation.name != "pypy":
        active_logger.debug("Skipping PyPy Astroid object_build patch on non-PyPy")
        return

    import astroid
    import pylint

    pylint_version = getattr(pylint, "__version__", "0")
    astroid_version = getattr(astroid, "__version__", "0")
    if not _is_supported_version(pylint_version, 4, 5) or not _is_supported_version(
        astroid_version,
        4,
        5,
    ):
        _handle_unsupported_versions(active_logger, pylint_version, astroid_version)
        return

    global _PATCH_INSTALLED
    with _PATCH_LOCK:
        _validate_astroid_shape(active_logger)
        if _PATCH_INSTALLED:
            active_logger.debug("PyPy Astroid object_build patch already installed")
            return
        active_logger.info(
            "Installing PyPy Astroid object_build patch (pylint %s, astroid %s)",
            pylint_version,
            astroid_version,
        )
        raw_building.InspectBuilder.object_build = (  # type: ignore[invalid-assignment]
            _object_build_without_pypy_descriptor_aliases
        )
        _PATCH_INSTALLED = True


def _is_supported_version(version: str, minimum: int, maximum: int) -> bool:
    """Return whether *version* is within the supported major range."""
    try:
        major = int(version.split(".", maxsplit=1)[0])
    except ValueError:
        return False
    return minimum <= major < maximum


def _handle_unsupported_versions(
    logger: logging.Logger,
    pylint_version: str,
    astroid_version: str,
) -> None:
    """Warn or fail when the installed Pylint/Astroid versions are unknown."""
    message = (
        "Skipping PyPy Astroid object_build patch for unsupported versions "
        f"(pylint {pylint_version}, astroid {astroid_version})"
    )
    if _is_strict_mode_enabled():
        logger.error(message)
        raise RuntimeError(message)
    logger.warning(message)


def _is_strict_mode_enabled() -> bool:
    """Return whether package-specific strict mode is explicitly enabled."""
    return os.environ.get(_STRICT_ENV_VAR, "0").strip() == "1"


def _validate_astroid_shape(logger: logging.Logger | None = None) -> None:
    """Validate that Astroid still exposes the inspected patch target."""
    active_logger = logger or logging.getLogger(__name__)
    if not hasattr(raw_building, "InspectBuilder"):
        message = "astroid.raw_building.InspectBuilder is required"
        active_logger.error(message)
        raise RuntimeError(message)
    object_build = getattr(raw_building.InspectBuilder, "object_build", None)
    if not callable(object_build):
        message = "InspectBuilder.object_build must be callable"
        active_logger.error(message)
        raise RuntimeError(message)  # noqa: TRY004
    signature = inspect.signature(object_build)
    if tuple(signature.parameters)[:3] != ("self", "node", "obj"):
        message = "InspectBuilder.object_build signature is unsupported"
        active_logger.error("%s: %s", message, signature)
        raise RuntimeError(message)
