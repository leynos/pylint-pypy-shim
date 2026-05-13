# pylint-pypy-shim users' guide

`pylint-pypy-shim` installs a small Astroid compatibility patch before Pylint
inspects live Python objects under PyPy. The package is deliberately narrow: it
changes one Astroid object-building path on PyPy and does nothing on CPython.

Use the shim when a project already runs Pylint under PyPy, or when CI needs a
PyPy lint job that behaves like the CPython job without carrying a local
wrapper script in every repository.

## Installation

Install the shim into the same environment that runs Pylint:

```bash
python -m pip install pylint-pypy-shim
```

For an isolated PyPy invocation with `uv`, ask `uv` to create the tool
environment with PyPy:

```bash
uv tool run --python pypy --from pylint-pypy-shim pylint-pypy --version
```

The package depends on Pylint 4.x and Astroid 4.x. Those dependency bounds are
declared in `pyproject.toml`, so normal package installation will resolve a
compatible pair.

## Adoption path 1: Pylint plugin

Use the plugin when a project already has a Pylint command and configuration.
Add the plugin to `pyproject.toml`:

```toml
[tool.pylint]
load-plugins = ["pylint_pypy_shim"]
```

Then run Pylint under PyPy as usual:

```bash
uv tool run --python pypy --from 'pylint==4.*' pylint your_package
```

The plugin entry point calls `install_patch(...)` and returns. It does not add
messages, disable checkers, or alter Pylint configuration.

## Adoption path 2: CLI wrapper

Use the CLI wrapper when the invocation itself should install the patch before
delegating to Pylint:

```bash
pylint-pypy your_package
```

For a local checkout, run the module directly:

```bash
uv run python -m pylint_pypy_shim.cli your_package
```

The wrapper returns Pylint's exit status. A clean run returns `0`; lint
violations or Pylint failures return Pylint's non-zero status so CI can fail in
the normal way.

## Runtime behaviour

The patch is guarded by `sys.implementation.name == "pypy"`.

- On PyPy with supported Pylint and Astroid versions, the shim replaces
  `astroid.raw_building.InspectBuilder.object_build`.
- On CPython, the shim logs a debug message and returns without changing
  Astroid.
- On unsupported versions, the shim logs a warning and returns without changing
  Astroid unless strict mode is enabled.

The patched object builder:

- ignores non-string entries returned by `dir(obj)`;
- treats `AttributeError` and `TypeError` from `getattr(obj, alias)` as a
  skipped member and lets Astroid attach a dummy node;
- preserves PyPy's special `__class_getitem__` route through Astroid's builtin
  member builder;
- otherwise delegates to Astroid's existing class, descriptor, constant,
  routine, module-like, and dummy builders.

## Supported versions

| Runtime       | Pylint         | Astroid        | Behaviour                       |
| ------------- | -------------- | -------------- | ------------------------------- |
| PyPy 3.11     | `>=4,<5`       | `>=4,<5`       | Patch is installed              |
| CPython 3.11+ | `>=4,<5`       | `>=4,<5`       | Patch is skipped                |
| PyPy 3.11     | Other versions | Other versions | Patch is skipped with a warning |

## Strict mode

Set `PYLINT_PYPY_SHIM_STRICT=1` to fail fast when the current Pylint or Astroid
version is outside the supported range:

```bash
PYLINT_PYPY_SHIM_STRICT=1 pylint-pypy your_package
```

Only the exact value `1` enables strict mode. Other values, such as `true` or
`yes`, are treated as disabled so unrelated shell configuration does not
accidentally activate the failure path.

## Choosing plugin or CLI

Prefer the plugin when:

- the project already runs `pylint` directly;
- the configuration should live with the rest of the Pylint settings;
- the same invocation should work for local developers and CI.

Prefer the CLI wrapper when:

- the command line should make the shim behaviour explicit;
- a temporary PyPy lint run is needed without editing project configuration;
- the wrapper is easier to place into a CI job than a Pylint plugin setting.

Both paths call the same public `install_patch(...)` function, so their
patching behaviour is identical.

## Troubleshooting

If the patch is not being installed, check these items first:

- Confirm the command is running under PyPy, not CPython.
- Confirm Pylint is version 4.x and Astroid is version 4.x.
- Run with logging enabled and look for `pylint_pypy_shim` warnings.
- Use `PYLINT_PYPY_SHIM_STRICT=1` in CI when unsupported versions should stop
  the job immediately.

If Pylint itself exits with a non-zero status, `pylint-pypy` propagates that
status. Treat it like a normal Pylint failure and inspect the reported
diagnostics.
