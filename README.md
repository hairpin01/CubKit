# CubKit

CubKit is a small toolkit for MCUB module development.

The first supported workflow is a builder that packs a multi-file module project
into a single `.py` artifact that can still be loaded by MCUB as a normal module.

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
  main.py
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
entrypoint = "main.py"
package = "my_module_lib"
assets = "assets"
```

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
