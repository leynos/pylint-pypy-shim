# pylint-pypy-shim

`pylint-pypy-shim` installs a small Astroid monkey patch for running Pylint
under PyPy. The patch replaces Astroid's inspected
`InspectBuilder.object_build` path with a PyPy-aware implementation that
ignores non-string `dir()` entries and treats PyPy descriptor `getattr`
failures as skipped members.

The shim is intentionally narrow. It only applies on PyPy and is a no-op on
CPython.

## Supported versions

| Runtime       | Pylint         | Astroid        | Behaviour                       |
| ------------- | -------------- | -------------- | ------------------------------- |
| PyPy 3.11     | `>=4,<5`       | `>=4,<5`       | Patch is installed              |
| CPython 3.11+ | `>=4,<5`       | `>=4,<5`       | Patch is skipped                |
| PyPy 3.11     | Other versions | Other versions | Patch is skipped with a warning |

Set `STRICT=1` to make unsupported Pylint or Astroid versions fail fast instead
of logging a warning and continuing without the patch.

## Pylint plugin

Add the plugin to your Pylint configuration and run Pylint under PyPy:

```toml
[tool.pylint]
load-plugins = ["pylint_pypy_shim"]
```

Then run Pylint normally with a PyPy interpreter:

```bash
uv tool run --python pypy --from 'pylint==4.*' pylint your_package
```

The plugin only calls `install_patch(...)`. It does not add messages, change
configuration, or alter Pylint's reporting policy.

## CLI wrapper

The package also exposes a `pylint-pypy` command that installs the patch before
delegating to Pylint:

```bash
uv tool run --python pypy --from pylint-pypy-shim pylint-pypy your_package
```

For local source checkouts, the same wrapper can be invoked as a module:

```bash
python -m pylint_pypy_shim.cli your_package
```

The wrapper returns Pylint's exit code.
