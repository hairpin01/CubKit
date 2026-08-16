"""High-level build orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .bundler import build_zip_payload
from .collector import collect_bundle_files, validate_bundle_files
from .manifest import load_manifest
from .renderer import default_artifact_stem, render_module
from .security import validate_no_secrets

BuildProgress = Callable[[Path, int, int], None]
BuildStatus = Callable[[str], None]
DependencyProgress = Callable[[str, int, int], None]


def check_project(project_dir: Path, *, profile: str | None = None, run_configured_hooks: bool = True) -> int:
    """Validate a CubKit project and return the number of embedded files."""

    manifest = load_manifest(project_dir)
    if run_configured_hooks:
        from .hooks import run_hooks
        run_hooks(manifest, "pre_check", profile)
    files = collect_bundle_files(manifest)
    validate_bundle_files(files)
    if manifest.fail_on_secrets:
        validate_no_secrets(files)
    if run_configured_hooks:
        run_hooks(manifest, "post_check", profile)
    return len(files)


def build_project(
    project_dir: Path,
    output: Path | None = None,
    progress: BuildProgress | None = None,
    status: BuildStatus | None = None,
    dependency_progress: DependencyProgress | None = None,
    profile: str | None = None,
    run_configured_hooks: bool = True,
) -> Path:
    """Build *project_dir* and return the generated artifact path."""

    project_dir = project_dir.resolve()
    manifest = load_manifest(project_dir)
    if run_configured_hooks:
        from .hooks import run_hooks
        run_hooks(manifest, "pre_build", profile)
    if manifest.libs and status is not None:
        status("collecting dependencies...")
    files = collect_bundle_files(manifest, dependency_progress=dependency_progress)
    total = 1 + len(files)
    done = 0

    def report(path: Path) -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(path, done, total)

    report(manifest.entrypoint)
    validate_bundle_files(files)
    if manifest.fail_on_secrets:
        validate_no_secrets(files)
    for item in files:
        report(item.source)
    payload = build_zip_payload(files)
    rendered = render_module(manifest, payload, bundle_files=files)

    if output is not None:
        output_path = output.resolve()
    elif profile == "debug" and manifest.debug_out is not None:
        output_path = manifest.debug_out
    elif profile == "release" and manifest.release_out is not None:
        output_path = manifest.release_out
    elif manifest.out is not None:
        output_path = manifest.out
    else:
        output_path = project_dir / "dist" / f"{default_artifact_stem(manifest)}.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    if run_configured_hooks:
        from .hooks import run_hooks
        run_hooks(manifest, "post_build", profile, output_path)
    return output_path
