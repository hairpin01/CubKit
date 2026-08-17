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
cubkit types my_module
cubkit check my_module
cubkit build my_module
```

The build output is written to `my_module/dist/<module_id>.py` by default.

`cubkit types` downloads MCUB `core/lib/types/*.py` into `core/lib/types/` for
local type hints and adds `core/` to `.gitignore`.

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
  locales/
    en.yaml
    ru.yaml
```

`cubkit.toml`:

```toml
format = 2

[module]
id = "my_module"
name = "My Module"
version = "0.1.0"
author = "unknown"
description = "Built with CubKit"
requires = ["aiohttp"]

[bundle]
source = "src"
entrypoint = "main.py"
output = "dist/my_module.py"
debug_output = "dist/my_module-debug.py"
release_output = "../module-repository/my_module.py"
packages = ["my_module_lib"]
sources = ["utils.py"]
assets = "assets"
locales = "locales"
sign = true
include = ["resources/**/*.json"]
exclude = ["resources/private-*.json"]
fail_on_secrets = true

[libs.genipng]
type = "local"
path = "vendor/genipng"

[hooks]
pre_build = ["python", "scripts/generate_version.py"]

[hooks.release]
post_build = ["python", "scripts/publish.py", "{output}"]
```

When `source = "src"` is set, code paths like `entrypoint`, `packages` and `sources`
are resolved inside `src/`. Non-code assets and locales remain relative to the
project root.
When `output` is set, `cubkit build` writes there by default. CLI `-o/--output`
overrides the manifest output.
Use `cubkit build --debug` or `cubkit build --release` to select the matching
profile output. Profile hooks (for example `[hooks.release]`) run only when that
profile is selected.

`include` adds project-relative glob matches to the bundle; `exclude` removes
matches from the final bundle. With `fail_on_secrets = true`, `check` and `build`
stop when a bundled text file resembles a private key, API token, or Telegram bot
token. Use `cubkit lint` to check that the entrypoint has an MCUB `ModuleBase`/
`Module` subclass or a function-style `main()`/`register()` entrypoint.
External-import validation and automatic pre-build linting are configured with
`[lint]`; see the full documentation for examples.

For a byte-identical artifact from unchanged inputs, use `cubkit build
--reproducible`. It embeds the SHA-256 of the manifest in the generated file.

Legacy flat manifests remain supported. Convert one with:

```bash
cubkit migrate my_module
```

CubKit saves the original as `cubkit.toml.bak`. Format 2 rejects mixed legacy
root fields so configuration mistakes fail explicitly.

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

## Runtime environment

Bundled modules have an isolated runtime facade available through a private
relative import:

```python
from core.lib.loader.module_base import ModuleBase
from cubkit import (
    assets,
    get_environment,
    load_strings,
    metadata,
    resource,
    root,
)


class Mod(ModuleBase):
    strings = load_strings()

icon_path = resource("icon.png")
raw_icon = assets.read_bytes("icon.png")
module_name = metadata["name"]
assert root == get_environment()["root"]
```

`assets` provides safe path lookup plus `read_bytes`, `read_text`, and
`read_json`. Paths are constrained to the configured assets directory.
`metadata` is a read-only mapping generated from `cubkit.toml` and literal
entrypoint metadata.

`load_strings()` returns the ordinary dictionary expected by MCUB's native
`ModuleBase.strings`. Localization files are direct children of `locales` and
use plain lowercase language filenames such as `en.yaml`, `ru.yaml`, and
`uk.yaml`; regional names such as `ru-RU` are intentionally rejected. YAML,
JSON, and TOML mappings are supported:

```yaml
help: "Help for {name}"
done: "Done"
```

CubKit builds these files into `{"en": {...}, "ru": {...}}`; MCUB then selects
the language and English fallback itself. Inside module methods use the normal
`self.strings("help", name=...)` or `self.strings["done"]` API. CubKit does not
add a second translation runtime.

Local libraries declared in `[libs]` are vendored into the artifact and can be
imported with:

```python
from cubkit.lib import genipng
```

`package_pip`, `package_github`, and direct `url` descriptors are resolved with
`pip install --target` during build. Local `path` may point inside the project,
outside it with `../`, or to an absolute path. It can target a source package
directory, a `.py` module, a `.whl`, or a native extension file such as
`.so`/`.pyd`. If a local directory contains `pyproject.toml`, `setup.py`, or
`setup.cfg`, CubKit installs it with pip too, so its package dependencies are
vendored into the artifact. When libs are present, `cubkit build` prints
`collecting dependencies...` before bundling them.

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
