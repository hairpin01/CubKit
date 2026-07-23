"""Collect files for a CubKit bundle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import BuildError
from .manifest import Manifest

EXCLUDED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".session", ".session-journal"}
EXCLUDED_FILES = {".env"}


@dataclass(frozen=True)
class BundleFile:
    """A file that will be embedded into the generated module."""

    source: Path
    archive_name: str


def collect_bundle_files(manifest: Manifest) -> list[BundleFile]:
    """Collect package and asset files for *manifest*."""

    files: list[BundleFile] = []
    if manifest.package is not None:
        files.extend(_collect_tree(manifest.package, manifest.package.parent))
    if manifest.assets is not None:
        files.extend(_collect_tree(manifest.assets, manifest.assets.parent))
    return sorted(files, key=lambda item: item.archive_name)


def validate_bundle_files(files: list[BundleFile]) -> None:
    """Validate collected files and compile embedded Python sources."""

    seen: set[str] = set()
    for item in files:
        if item.archive_name in seen:
            raise BuildError(f"duplicate archive path: {item.archive_name}")
        seen.add(item.archive_name)
        if item.source.suffix == ".py":
            _compile_python(item.source)


def _collect_tree(root: Path, base: Path) -> list[BundleFile]:
    result: list[BundleFile] = []
    for path in root.rglob("*"):
        if _is_excluded(path):
            continue
        if not path.is_file():
            continue
        archive_name = PurePosixPath(path.relative_to(base).as_posix()).as_posix()
        result.append(BundleFile(source=path, archive_name=archive_name))
    return result


def _is_excluded(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return True
    if path.name in EXCLUDED_FILES:
        return True
    return any(path.name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def _compile_python(path: Path) -> None:
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        raise BuildError(f"syntax error in {path}: {exc.msg} at line {exc.lineno}") from exc
