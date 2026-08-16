"""Non-shell command hooks configured by a CubKit manifest."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from .errors import BuildError
from .manifest import Manifest, find_manifest


def run_hooks(manifest: Manifest, event: str, profile: str | None, output: Path | None = None) -> None:
    """Run common hooks and hooks specific to the selected build profile."""

    if manifest.hooks is None:
        return
    commands = list(manifest.hooks.common.get(event, ()))
    if profile is not None:
        commands.extend(manifest.hooks.profiles.get(profile, {}).get(event, ()))
    variables = {
        "project_dir": str(manifest.project_dir),
        "manifest": str(find_manifest(manifest.project_dir)),
        "module_id": manifest.module_id,
        "command": event.removeprefix("pre_").removeprefix("post_"),
        "output": str(output) if output is not None else "",
        "profile": profile or "default",
    }
    environment = os.environ | {f"CUBKIT_{key.upper()}": value for key, value in variables.items()}
    for command in commands:
        try:
            argv = [argument.format(**variables) for argument in command]
        except KeyError as exc:
            raise BuildError(f"unknown hook placeholder: {exc.args[0]}") from exc
        try:
            subprocess.run(argv, cwd=manifest.project_dir, env=environment, check=True)
        except FileNotFoundError as exc:
            raise BuildError(f"hook command not found: {argv[0]}") from exc
        except subprocess.CalledProcessError as exc:
            raise BuildError(f"{event} hook failed ({exc.returncode}): {' '.join(argv)}") from exc
