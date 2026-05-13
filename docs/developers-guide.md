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
- successful patch installation: info;
- repeated installation: debug;
- ignored member-resolution failures: debug;
- legacy Pylint CLI API fallback: debug;
- non-integer legacy `SystemExit.code`: warning.

## State and concurrency

The patch is process-global because it replaces an Astroid class method. The
module keeps `_PATCH_INSTALLED` to make installation idempotent and protects
the check-and-set sequence with `_PATCH_LOCK`.

This lock covers the critical section where Astroid shape is validated,
`_PATCH_INSTALLED` is checked, and `InspectBuilder.object_build` is replaced.
That allows the Pylint plugin and the CLI wrapper to call `install_patch(...)`
in the same process without racing or reassigning the patch.

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
