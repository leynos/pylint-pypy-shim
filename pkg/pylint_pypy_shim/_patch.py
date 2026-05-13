"""PyPy-specific Astroid object builder compatibility patch.

Purpose
-------
Astroid's ``InspectBuilder.object_build`` can fail under PyPy because PyPy
exposes descriptor aliases, such as ``__class_getitem__``, that CPython does
not expose in the same way. Those aliases can produce unexpected
``AttributeError`` or ``TypeError`` failures during live-object inspection.

Utility
-------
This module provides a PyPy-safe replacement object builder that filters
non-string ``dir()`` entries, suppresses known ``getattr`` failures, and routes
PyPy's ``__class_getitem__`` alias through Astroid's builtin-member path.
``install_patch()`` is the public entry point: it monkey-patches Astroid once,
is guarded by ``_PATCH_INSTALLED`` and ``_PATCH_LOCK``, and only activates when
running on PyPy.

Examples
--------
Install the patch early in program startup, before Astroid starts building
modules:

```python
import logging

from pylint_pypy_shim._patch import install_patch

install_patch(logger=logging.getLogger(__name__))
# Astroid's InspectBuilder.object_build is now PyPy-safe.
```

Side effects
------------
``install_patch()`` mutates
``astroid.raw_building.InspectBuilder.object_build`` globally. Calling it more
than once is a no-op, and unsupported runtimes or dependency versions leave
Astroid unchanged unless strict mode is enabled.

"""

from __future__ import annotations

import collections
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
_LOG = logging.getLogger(__name__)
_PATCH_LOCK = threading.Lock()
_PATCH_INSTALLED = False
_METRICS: collections.Counter[str] = collections.Counter()


class PatchError(RuntimeError):
    """Base exception for pylint-pypy-shim patch errors."""


def _cached_child_type_error(member: object, child: object) -> str:
    """Describe a cached Astroid child type invariant failure."""
    return f"_done entry for {member!r} must be a ClassDef, got {type(child).__name__}"


def get_metrics() -> collections.Counter[str]:
    """Return a snapshot of patch routing and resolution counters."""
    return _METRICS.copy()


def _record_metric(name: str) -> None:
    """Increment an internal diagnostic counter."""
    _METRICS[name] += 1


def _build_builtin_child(
    self: raw_building.InspectBuilder,
    node: nodes.Module | nodes.ClassDef,
    member: object,
    alias: str,
) -> nodes.NodeNG | None:
    """Build a child for builtins unless Astroid treats it as imported."""
    if self.imported_member(node, member, alias):
        _record_metric("dispatch.imported")
        return None
    _record_metric("dispatch.builtin")
    return object_build_methoddescriptor(
        node,
        typ.cast("typ.Any", member),
    )


def _build_class_child(
    self: raw_building.InspectBuilder,
    node: nodes.Module | nodes.ClassDef,
    member: type,
    alias: str,
) -> nodes.ClassDef | None:
    """Build or reuse a class child unless Astroid treats it as imported."""
    if self.imported_member(node, member, alias):
        _record_metric("dispatch.imported")
        return None
    if member in self._done:
        child = self._done[member]
        if not isinstance(child, nodes.ClassDef):
            raise PatchError(_cached_child_type_error(member, child))
        _record_metric("dispatch.class.cached")
        return child
    _record_metric("dispatch.class.built")
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
        _record_metric("dispatch.const.special")
        return None
    _record_metric("dispatch.const")
    return nodes.const_factory(member)  # type: ignore[invalid-return-type]


def _attach_child_node(
    node: nodes.Module | nodes.ClassDef,
    alias: str,
    child: nodes.NodeNG,
) -> None:
    """Attach *child* to *node* under *alias* unless it is already present."""
    if child not in node.locals.get(alias, ()):
        node.add_local_node(child, alias)


def _dispatch_member_to_child(
    self: raw_building.InspectBuilder,
    node: nodes.Module | nodes.ClassDef,
    member: object,
    alias: str,
) -> nodes.NodeNG | None:
    """Dispatch members to the matching Astroid builder."""
    logger = logging.getLogger(__name__)
    match member:
        case _ if inspect.isbuiltin(member):
            logger.debug("Dispatching %s as builtin member of %r", alias, node)
            child = _build_builtin_child(self, node, member, alias)
        case _ if inspect.isclass(member):
            logger.debug("Dispatching %s as class member of %r", alias, node)
            child = _build_class_child(self, node, member, alias)
        case _ if inspect.ismethoddescriptor(member):
            logger.debug("Dispatching %s as method descriptor of %r", alias, node)
            _record_metric("dispatch.method_descriptor")
            child = object_build_methoddescriptor(node, member)
        case _ if inspect.isdatadescriptor(member):
            logger.debug("Dispatching %s as data descriptor of %r", alias, node)
            _record_metric("dispatch.data_descriptor")
            child = object_build_datadescriptor(
                node,
                typ.cast("type", member),
            )
        case _ if isinstance(member, tuple(node_classes.CONST_CLS)):
            logger.debug("Dispatching %s as const member of %r", alias, node)
            child = _build_const_child(node, member, alias)
        case _ if inspect.isroutine(member):
            logger.debug("Dispatching %s as routine member of %r", alias, node)
            _record_metric("dispatch.routine")
            child = _build_from_function(
                node,
                member,
                typ.cast("typ.Any", self._module),
            )
        case _ if _safe_has_attribute(member, "__all__"):
            logger.debug("Dispatching %s as module-like member of %r", alias, node)
            _record_metric("dispatch.module_like")
            child = build_module(alias)
            self.object_build(child, typ.cast("types.ModuleType | type", member))
        case _:
            logger.debug("Dispatching %s as dummy member of %r", alias, node)
            _record_metric("dispatch.dummy")
            child = build_dummy(member)
    return child


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
        _record_metric("resolve.getattr_failure")
        _LOG.debug(
            "astroid getattr failed on %r for alias %r; treating as skip",
            obj,
            alias,
        )
        if logger is not None:
            logger.debug("Skipping %r from %r: %s", alias, obj, error)
        return None, pypy__class_getitem__, True
    if inspect.ismethod(member) and not pypy__class_getitem__:
        member = member.__func__
        _record_metric("resolve.bound_method_unwrapped")
    _record_metric("resolve.success")
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
                _record_metric("resolve.non_string_dir_entry")
                logger.debug("Ignoring non-string dir entry %r from %r", alias, obj)
                continue
            member, pypy__class_getitem__, skip = _resolve_member(
                node, obj, alias, logger
            )
            if skip:
                _record_metric("dispatch.dummy.getattr_failure")
                logger.debug("Attaching dummy node for skipped %s from %r", alias, obj)
                attach_dummy_node(node, alias)
                continue
            if pypy__class_getitem__:
                logger.debug("Dispatching %s through PyPy builtin path", alias)
                child = _build_builtin_child(self, node, member, alias)
            else:
                child = _dispatch_member_to_child(self, node, member, alias)
            if child is not None:
                _attach_child_node(node, alias, child)


def install_patch(logger: logging.Logger | None = None) -> None:
    """Install the PyPy Astroid object-build patch when versions are supported."""
    active_logger = logger or _LOG
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
        typ.cast(
            "typ.Any", raw_building.InspectBuilder
        ).object_build = _object_build_without_pypy_descriptor_aliases
        _PATCH_INSTALLED = True
        _LOG.info("astroid InspectBuilder.object_build patched for PyPy")
        _LOG.info(
            "pylint=%s astroid=%s runtime=%s",
            pylint_version,
            astroid_version,
            sys.implementation.name,
        )


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
        raise PatchError(message)
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
        raise PatchError(message)
    object_build = getattr(raw_building.InspectBuilder, "object_build", None)
    if not callable(object_build):
        message = "InspectBuilder.object_build must be callable"
        active_logger.error(message)
        raise PatchError(message)
    signature = inspect.signature(object_build)
    if tuple(signature.parameters)[:3] != ("self", "node", "obj"):
        message = "InspectBuilder.object_build signature is unsupported"
        active_logger.error("%s: %s", message, signature)
        raise PatchError(message)
