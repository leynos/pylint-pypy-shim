# pylint-pypy-shim developers' guide

This guide documents the internal structure and maintenance expectations for
`pylint-pypy-shim`. It complements the user-facing workflow in
[the users' guide](users-guide.md).

## Package layout

The runtime package lives under `pkg/pylint_pypy_shim/`:

- `__init__.py` exports the package version.
- `_version.py` keeps the centralized `__version__` constant.
- `_patch.py` owns the Astroid monkey patch and the public
  `install_patch(...)` function.
- `plugin.py` exposes the Pylint plugin `register(...)` entry point.
- `cli.py` exposes the `pylint-pypy` wrapper.

The repository root also contains `tools/pylint_pypy.py`, a source-tree
compatibility wrapper used by subprocess smoke tests and local checkout runs.
It bootstraps `pkg/` onto `sys.path` before importing `pylint_pypy_shim.cli`.

Tests live under `tests/`. Behaviour tests use `pytest-bdd` features in
`tests/features/` and step definitions in `tests/steps/`.

## Patch design

The shim targets `astroid.raw_building.InspectBuilder.object_build` because
that is the inspected Astroid path that walks `dir(obj)` and resolves each
member with `getattr`.

The replacement object builder keeps Astroid's existing routing model:

- builtins go through `object_build_methoddescriptor`;
- classes go through `object_build_class` and recursive `object_build`;
- method and data descriptors go through Astroid's descriptor builders;
- constants go through `nodes.const_factory`;
- routines go through `_build_from_function`;
- module-like values exposing `__all__` go through `build_module`;
- everything else goes through `build_dummy`.

The PyPy-specific behaviour is limited to two compatibility guards:

- ignore non-string `dir(obj)` entries before member resolution;
- treat `AttributeError` and `TypeError` from `getattr(obj, alias)` as skipped
  members, leaving Astroid to attach a dummy node.

### Logger binding

`_active_logger(logger)` is the single authoritative logger-selection helper in
`_patch.py`. It returns the caller-provided logger when one is supplied and
falls back to the module-level `_LOG` logger otherwise.

`_object_build_factory(logger)` is a closure factory that captures the selected
logger at install time and returns the patched `object_build` method. This
keeps the bound logger active after patch installation without storing it in
additional module-level state.

`_object_build_with_logger(self, node, obj, logger)` is the inner
implementation delegate used by both the factory-produced closure and
`_object_build_without_pypy_descriptor_aliases`. It accepts an explicit logger
so per-alias debug messages route through the correct handler.

Callers that pass a logger to `install_patch(...)` continue to receive debug
and info messages from the patched builder after installation.

## Installation guard

`install_patch(...)` applies the patch only when all of these conditions are
true:

- `sys.implementation.name == "pypy"`;
- Pylint is 4.x;
- Astroid is 4.x;
- `astroid.raw_building.InspectBuilder.object_build` still has the expected
  shape.

Unsupported versions log a warning and return without patching. When
`PYLINT_PYPY_SHIM_STRICT=1`, unsupported versions raise `RuntimeError` instead.
Only the exact value `1` enables strict mode.

## Validation and observability

Do not use `assert` for runtime validation in the shim. Python optimisation can
remove assertions, so `_patch.py` raises `RuntimeError` for unsupported Astroid
shape and logs the reason first.

Operational logging boundaries:

- non-PyPy runtime: debug log and no-op;
- unsupported versions: warning, or error plus `RuntimeError` in strict mode;
- successful patch installation: info log for the patch event and a second
  info log with the active Pylint version, Astroid version, and runtime;
- repeated installation: debug;
- ignored member-resolution failures: debug;
- legacy Pylint CLI API fallback: debug;
- non-integer legacy `SystemExit.code`: warning.

`_patch.py` owns the module-level `_LOG` logger. Public callers may pass a
logger into `install_patch(...)`; when they do not, the installer falls back to
`_LOG`. Helper paths that accept a logger should keep using the injected logger
for request-specific context and `_LOG` only for module-level events.

## State and concurrency

The patch is process-global because it replaces an Astroid class method. The
module keeps `_PATCH_INSTALLED` to make installation idempotent and protects
the check-and-set sequence with `_PATCH_LOCK`.

This lock covers the critical section where Astroid shape is validated,
`_PATCH_INSTALLED` is checked, and `InspectBuilder.object_build` is replaced.
That allows the Pylint plugin and the CLI wrapper to call `install_patch(...)`
in the same process without racing or reassigning the patch.

`_METRICS` is a module-level `Counter[str]` that accumulates named event counts
across all object-build calls. `_METRICS_LOCK` serialises every read and write
to that counter so concurrent `object_build` invocations report consistent
counts. `_record_metric(name)` is the single write path and always acquires the
lock before incrementing. `get_metrics()` acquires the same lock and returns a
snapshot copy of `_METRICS`; it is currently internal, but available for
diagnostic use.

## Testing expectations

The suite should cover public behaviour and the private routing helpers that
mirror Astroid's internal dispatch. Keep these categories in place:

- `install_patch(...)` paths for non-PyPy, supported PyPy, idempotency,
  unsupported versions, strict mode, and Astroid shape validation;
- CLI `main(...)` paths for current Pylint, legacy `Run` APIs, and safe
  `SystemExit.code` conversion;
- Pylint plugin `register(...)`;
- dispatch routing for builtins, classes, descriptors, constants, routines,
  module-like values, and dummy fallback;
- object-builder cache behaviour;
- PyPy compatibility invariants, including property tests for non-string
  `dir()` entries and supported `getattr` failures;
- source-tree wrapper behaviour for `tools/pylint_pypy.py`;
- behaviour tests proving the CLI propagates both success and failure statuses.

Run the normal project gates before committing:

```bash
make check-fmt
make lint
make typecheck
make test
make markdownlint
make nixie
make build-release
```

For PyPy validation, run:

```bash
uv tool run --python pypy --from 'pylint==4.*' --with-editable . \
  python -m pylint_pypy_shim.cli --version
uv run --python pypy --group dev pytest -v
```

Remove generated `dist/`, egg-info, and `__pycache__` artefacts after build or
test runs before committing.

## Build and packaging

The package uses Hatchling with `pkg/pylint_pypy_shim` as the wheel package
root. `pyproject.toml` declares:

- the Pylint plugin entry point under `[project.entry-points."pylint"]`;
- the `pylint-pypy` console script under `[project.scripts]`;
- Pylint and Astroid runtime dependency bounds;
- development dependencies for linting, typing, unit tests, behaviour tests,
  and property tests.

The `build-release` Make target runs the build frontend inside the managed `uv`
environment so the build tool dependency is resolved consistently with the rest
of the project.
