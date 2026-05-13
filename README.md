# pylint-pypy-shim

*A tiny PyPy safety shim for running Pylint when Astroid trips over runtime
introspection.*

`pylint-pypy-shim` patches one narrow Astroid inspection path before Pylint
runs. It exists for PyPy users who want the same linting workflow they already
use, without carrying a project-local wrapper script around like linting
hand-luggage.

______________________________________________________________________

## Why pylint-pypy-shim?

Astroid's `InspectBuilder.object_build` walks `dir(obj)` and resolves each
member with `getattr`. Under PyPy, that route can expose descriptor aliases
that do not behave like CPython's runtime objects.

- **PyPy-aware linting**: ignore non-string `dir()` entries before member
  resolution.
- **Graceful descriptor handling**: treat PyPy `getattr` failures as skipped
  members, leaving Astroid's caller to attach a dummy node.
- **No CPython surprises**: the patch is a no-op outside PyPy.
- **Two adoption paths**: load it as a Pylint plugin or run through the
  `pylint-pypy` wrapper.

______________________________________________________________________

## Quick start

### Installation

Install the package into the same environment that runs Pylint:

```bash
python -m pip install pylint-pypy-shim
```

For an isolated PyPy run with `uv`:

```bash
uv tool run --python pypy --from pylint-pypy-shim pylint-pypy --version
```

### Basic usage

Use the plugin when you already have a Pylint command:

```toml
[tool.pylint]
load-plugins = ["pylint_pypy_shim"]
```

Then run Pylint with PyPy:

```bash
uv tool run --python pypy --from 'pylint==4.*' pylint your_package
```

Or use the wrapper, which installs the patch before delegating to Pylint:

```bash
pylint-pypy your_package
```

For a local source checkout:

```bash
uv run python -m pylint_pypy_shim.cli your_package
```

______________________________________________________________________

## Features

- Pylint plugin entry point: `pylint_pypy_shim`.
- CLI wrapper: `pylint-pypy`.
- PyPy-only patch guard using `sys.implementation.name`.
- Version guard for Pylint 4.x and Astroid 4.x.
- `STRICT=1` mode for failing fast on unsupported Pylint or Astroid versions.
- Focused unit and behaviour tests for the PyPy Astroid edge cases.

______________________________________________________________________

## Supported versions

| Runtime       | Pylint         | Astroid        | Behaviour                       |
| ------------- | -------------- | -------------- | ------------------------------- |
| PyPy 3.11     | `>=4,<5`       | `>=4,<5`       | Patch is installed              |
| CPython 3.11+ | `>=4,<5`       | `>=4,<5`       | Patch is skipped                |
| PyPy 3.11     | Other versions | Other versions | Patch is skipped with a warning |

Set `STRICT=1` to make unsupported Pylint or Astroid versions fail fast instead
of logging a warning and continuing without the patch:

```bash
STRICT=1 pylint-pypy your_package
```

______________________________________________________________________

## Learn more

- [Users' Guide](docs/users-guide.md) — full project documentation
- [Scripting standards](docs/scripting-standards.md) — local scripting
  conventions
- [Agent instructions](AGENTS.md) — contribution and validation expectations

______________________________________________________________________

## Licence

ISC — see [LICENSE](LICENSE) for details.

______________________________________________________________________

## Contributing

Contributions welcome. Please read [AGENTS.md](AGENTS.md) before changing the
project, and run the documented quality gates before opening a review.
