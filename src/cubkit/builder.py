"""High-level build orchestration."""

from __future__ import annotations

from pathlib import Path

from .bundler import build_zip_payload
from .collector import collect_bundle_files, validate_bundle_files
from .manifest import load_manifest
from .renderer import default_artifact_stem, render_module


def check_project(project_dir: Path) -> int:
    """Validate a CubKit project and return the number of embedded files."""

    manifest = load_manifest(project_dir)
    files = collect_bundle_files(manifest)
    validate_bundle_files(files)
    return len(files)


def build_project(project_dir: Path, output: Path | None = None) -> Path:
    """Build *project_dir* and return the generated artifact path."""

    project_dir = project_dir.resolve()
    manifest = load_manifest(project_dir)
    files = collect_bundle_files(manifest)
    validate_bundle_files(files)
    payload = build_zip_payload(files)
    rendered = render_module(manifest, payload)

    output_path = output.resolve() if output is not None else project_dir / "dist" / f"{default_artifact_stem(manifest)}.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path
