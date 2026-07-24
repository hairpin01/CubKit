# CubKit

CubKit is a small toolkit for MCUB module development.

The first supported workflow is a builder that packs a multi-file module project
into a single `.py` artifact that can still be loaded by MCUB as a normal module.

Full usage guide with Mermaid build flow, main/lib examples and best practices:
[doc/index.md](doc/index.md).

## Install

```bash
pip install cubkit
```

For local development:

```bash
pip install -e .
```

## Quick start

```bash
cubkit init my_module
cubkit check my_module
cubkit build my_module
```

The build output is written to `my_module/dist/<module_id>.py` by default.

## Project layout

```text
my_module/
  cubkit.toml
  src/
    main.py
    mixin_command.py
    mixin_callback.py
    my_module_lib/
      __init__.py
      utils.py
  assets/
    icon.png
```

`cubkit.toml`:

```toml
id = "my_module"
name = "My Module"
version = "0.1.0"
author = "unknown"
description = "Built with CubKit"
# Optional source root for code files.
src = "src"
entrypoint = "main.py"
# One package dir or several package dirs are supported.
package = "my_module_lib"
# package = ["my_module_lib", "shared_helpers"]
assets = "assets"
# Optional root-private files for `from .utils import ...` in the entrypoint.
sources = ["utils.py"]
# Optional deterministic build signature comments.
sign = true
```

When `src = "src"` is set, code paths like `entrypoint`, `package` and `sources`
are resolved inside `src/`. Non-code assets remain relative to the project root.

CubKit writes MCUB metadata comments into the generated main artifact before the
bootstrap code, for example `# name:`, `# version:` and optional `# author:`,
`# description:`, `# requires:`, `# banner_url:` and `# scop:` lines. This keeps
function-style modules loadable by MCUB without manually duplicating manifest
metadata in `main.py`.

For class-style modules that inherit from MCUB `ModuleBase`/`Module`, CubKit uses
the literal class `name = "..."` attribute for `# name:` when it is present. This
keeps MCUB package metadata, the generated filename and the class-style module
name aligned.

Entrypoints may use private relative imports for bundled helpers:

```python
from .utils import helper
```

CubKit auto-detects simple relative imports from sibling files. You can also add
helper files manually to `sources`. Package directories may be declared as a
single string or a list. CubKit loads bundled helpers through a private package
name, so they do not collide with MCUB's global `utils`, `lib`, or other modules.

When `sign = true`, CubKit adds deterministic build integrity comments:

```python
# CubKit source sha256: ...
# CubKit payload sha256: ...
# CubKit signature: ...
```

Generated artifacts also include a lightweight source map/debug block, showing
where the entrypoint starts in the generated file and which bundled files were
embedded into the payload.

## Commands

- `cubkit init <path>` creates a starter module project.
- `cubkit check <path>` validates the manifest, entrypoint and bundle files.
- `cubkit build <path>` writes a single-file MCUB-compatible module artifact.

## Build model

CubKit keeps MCUB compatibility by leaving the module entrypoint as normal Python
code. Extra package files and assets are embedded into the generated file as a
deterministic zip payload. At import time the generated bootstrap extracts the
payload into a user cache directory and prepends it to `sys.path`, so imports such
as `from my_module_lib.utils import helper` work before the original entrypoint
code is executed.

The builder never executes module code during validation or build.
