"""Collect files for a CubKit bundle."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import BuildError
from .manifest import Manifest

EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
}
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
    files.extend(_discover_relative_import_sources(manifest.entrypoint))
    for source in manifest.sources:
        files.extend(_collect_source(source))
    for package in manifest.package:
        files.extend(_collect_tree(package, package.parent))
    if manifest.assets is not None:
        files.extend(_collect_tree(manifest.assets, manifest.assets.parent))
    return sorted(_dedupe(files), key=lambda item: item.archive_name)


def _collect_source(source: Path) -> list[BundleFile]:
    if source.is_file():
        return [
            BundleFile(
                source=source, archive_name=PurePosixPath(source.name).as_posix()
            )
        ]
    return _collect_tree(source, source)


def _discover_relative_import_sources(entrypoint: Path) -> list[BundleFile]:
    discovered: dict[Path, BundleFile] = {}
    project_base = entrypoint.parent
    pending = [entrypoint]
    seen: set[Path] = set()

    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)

        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except SyntaxError:
            continue

        for module_path in _iter_relative_import_paths(tree, source.parent):
            for bundle_file in _collect_relative_source(module_path, project_base):
                path = bundle_file.source.resolve()
                if path == entrypoint.resolve():
                    continue
                if path not in discovered:
                    discovered[path] = bundle_file
                    if path.suffix == ".py":
                        pending.append(path)

    return list(discovered.values())


def _collect_relative_source(source: Path, base: Path) -> list[BundleFile]:
    if source.is_file():
        archive_name = PurePosixPath(source.relative_to(base).as_posix()).as_posix()
        return [BundleFile(source=source, archive_name=archive_name)]
    return _collect_tree(source, base)


def _iter_relative_import_paths(tree: ast.AST, base_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level <= 0:
            continue

        relative_base = base_dir.joinpath(*([".."] * (node.level - 1))).resolve()
        module_parts = node.module.split(".") if node.module else []
        if module_parts:
            paths.extend(_resolve_relative_module(relative_base, module_parts))
        else:
            for alias in node.names:
                if alias.name == "*":
                    continue
                paths.extend(
                    _resolve_relative_module(relative_base, alias.name.split("."))
                )
    return paths


def _resolve_relative_module(base_dir: Path, parts: list[str]) -> list[Path]:
    module_base = base_dir.joinpath(*parts)
    candidates = [module_base.with_suffix(".py"), module_base / "__init__.py"]
    result: list[Path] = []
    for candidate in candidates:
        if candidate.is_file():
            result.append(candidate)
        elif candidate.name == "__init__.py" and candidate.parent.is_dir():
            result.append(candidate.parent)
    return result


def _dedupe(files: list[BundleFile]) -> list[BundleFile]:
    result: dict[str, BundleFile] = {}
    for item in files:
        result.setdefault(item.archive_name, item)
    return list(result.values())


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
        raise BuildError(
            f"syntax error in {path}: {exc.msg} at line {exc.lineno}"
        ) from exc
