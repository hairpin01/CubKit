"""Render single-file MCUB module artifacts."""

from __future__ import annotations

import ast
import base64
import hashlib
import re
import textwrap
from dataclasses import dataclass

from .manifest import Manifest


@dataclass(frozen=True)
class EntrypointMetadata:
    """Metadata discovered directly in the entrypoint source."""

    is_class_style: bool = False
    name: str | None = None
    version: str | None = None


def render_module(manifest: Manifest, payload: bytes) -> str:
    """Render a generated module from *manifest* and embedded *payload*."""

    entrypoint_source = manifest.entrypoint.read_text(encoding="utf-8")
    future_imports, entrypoint_source = _split_future_imports(entrypoint_source)
    entrypoint_metadata = read_entrypoint_metadata(entrypoint_source)
    payload_hash = hashlib.sha256(payload).hexdigest()
    encoded_payload = base64.b85encode(payload).decode("ascii") if payload else ""
    metadata = _render_metadata_header(
        manifest,
        name=entrypoint_metadata.name or manifest.name,
        version=entrypoint_metadata.version or manifest.version,
    )
    bootstrap = _render_bootstrap(
        manifest.module_id,
        payload_hash,
        encoded_payload,
        package_dir=manifest.package.name if manifest.package is not None else "",
    )
    future_block = f"\n{future_imports}\n" if future_imports else "\n"
    return f"{metadata}{future_block}{bootstrap}\n\n# ---- CubKit entrypoint: {manifest.entrypoint.name} ----\n{entrypoint_source}\n"


def default_artifact_stem(manifest: Manifest) -> str:
    """Return the default generated filename stem for *manifest*."""

    entrypoint_source = manifest.entrypoint.read_text(encoding="utf-8")
    entrypoint_metadata = read_entrypoint_metadata(entrypoint_source)
    return entrypoint_metadata.name or manifest.module_id


def _render_metadata_header(manifest: Manifest, *, name: str, version: str | None) -> str:
    lines = [f"# name: {name}"]
    if version:
        lines.append(f"# version: {version}")
    if manifest.author and manifest.author != "unknown":
        lines.append(f"# author: {manifest.author}")
    if manifest.description:
        lines.append(f"# description: {manifest.description}")
    if manifest.requires:
        lines.append(f"# requires: {', '.join(manifest.requires)}")
    if manifest.banner_url:
        lines.append(f"# banner_url: {manifest.banner_url}")
    if manifest.scop:
        lines.append(f"# scop: {manifest.scop}")
    return "\n".join(lines)


def read_entrypoint_metadata(source: str) -> EntrypointMetadata:
    header_name = _read_header_value(source, "name")
    header_version = _read_header_value(source, "version")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return EntrypointMetadata(name=header_name, version=header_version)

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(_is_module_base(base) for base in node.bases):
            return EntrypointMetadata(
                is_class_style=True,
                name=_read_class_string_attribute(node, "name") or header_name,
                version=_read_class_string_attribute(node, "version") or header_version,
            )
    return EntrypointMetadata(name=header_name, version=header_version)


def _read_header_value(source: str, key: str) -> str | None:
    match = re.search(rf"^\s*#\s*(?:{re.escape(key)}|meta\s+{re.escape(key)})\s*:\s*(.+)$", source, re.MULTILINE | re.IGNORECASE)
    if match:
        value = match.group(1).strip()
        if value:
            return value
    return None


def _read_class_string_attribute(node: ast.ClassDef, attribute: str) -> str | None:
    for statement in node.body:
        value: ast.expr | None = None
        if isinstance(statement, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == attribute for target in statement.targets):
                value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            if isinstance(statement.target, ast.Name) and statement.target.id == attribute:
                value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
            return value.value.strip()
    return None


def _is_module_base(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"ModuleBase", "Module"}
    if isinstance(node, ast.Attribute):
        return node.attr in {"ModuleBase", "Module"}
    return False


def _split_future_imports(source: str) -> tuple[str, str]:
    """Move entrypoint ``__future__`` imports before CubKit bootstrap code."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "", source

    lines = source.splitlines()
    future_ranges: list[tuple[int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" and node.level == 0:
            start = node.lineno
            end = getattr(node, "end_lineno", node.lineno)
            future_ranges.append((start, end))
            continue
        break

    if not future_ranges:
        return "", source

    removed_lines: set[int] = set()
    future_lines: list[str] = []
    for start, end in future_ranges:
        future_lines.extend(lines[start - 1 : end])
        removed_lines.update(range(start, end + 1))

    remaining = [line for index, line in enumerate(lines, start=1) if index not in removed_lines]
    return "\n".join(future_lines), "\n".join(remaining).lstrip("\n")


def _render_bootstrap(module_id: str, payload_hash: str, encoded_payload: str, *, package_dir: str) -> str:
    header = f"""# Generated by CubKit. Do not edit this header by hand.
# CubKit repository: https://github.com/hairpin01/CubKit
# CubKit build notes:
# - Metadata comments above were generated/normalized from cubkit.toml and entrypoint code.
# - Bundled helper files are stored below as a base85-encoded zip payload.
# - On import, CubKit verifies the payload SHA256 and extracts it into CUBKIT_CACHE_DIR or ~/.cache/cubkit.
# - CubKit import-debug comments below explain sys.path/package wiring for private relative imports.
"""
    if not encoded_payload:
        return (
            header
            +
            f"__cubkit_module_id__ = {module_id!r}\n"
            f"__cubkit_package_dir__ = {package_dir!r}\n"
            '__cubkit_bundle_sha256__ = ""'
        )

    wrapped_payload = "\n".join(textwrap.wrap(encoded_payload, width=100))
    return f'''{header}__cubkit_module_id__ = {module_id!r}
__cubkit_package_dir__ = {package_dir!r}
__cubkit_bundle_sha256__ = {payload_hash!r}
__cubkit_bundle_b85__ = """
{wrapped_payload}
"""

def __cubkit_bootstrap__():
    # CubKit import-debug: this function is generated by CubKit.
    # It prepares bundled files before the real MCUB module code below runs.
    import base64
    import hashlib
    import os
    import sys
    import zipfile
    from pathlib import Path

    # CubKit import-debug: decode the embedded base85 zip payload.
    data = base64.b85decode("".join(__cubkit_bundle_b85__.split()).encode("ascii"))
    digest = hashlib.sha256(data).hexdigest()
    # CubKit import-debug: fail fast if the embedded payload was corrupted.
    if digest != __cubkit_bundle_sha256__:
        raise RuntimeError("CubKit embedded bundle checksum mismatch")

    # CubKit import-debug: cache extraction avoids rewriting helper files on every import.
    cache_root = Path(os.environ.get("CUBKIT_CACHE_DIR", Path.home() / ".cache" / "cubkit"))
    bundle_dir = cache_root / __cubkit_module_id__ / digest
    marker = bundle_dir / ".cubkit-extracted"
    if not marker.exists():
        bundle_dir.mkdir(parents=True, exist_ok=True)
        archive_path = bundle_dir / "bundle.zip"
        archive_path.write_bytes(data)
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(bundle_dir)
        marker.write_text(digest, encoding="utf-8")

    # CubKit import-debug: expose extracted top-level files for normal absolute imports.
    bundle_path = str(bundle_dir)
    if bundle_path not in sys.path:
        sys.path.insert(0, bundle_path)

    # CubKit import-debug: build private package search paths for `from .utils import ...`.
    relative_import_paths = [bundle_path]
    if __cubkit_package_dir__:
        package_path = bundle_dir / __cubkit_package_dir__
        if package_path.is_dir():
            relative_import_paths.insert(0, str(package_path))

    # CubKit import-debug: mark the generated main module as package-like.
    # This prevents accidental imports from global MCUB packages named `utils`, `lib`, etc.
    module_globals = globals()
    module_globals["__path__"] = relative_import_paths
    module_globals["__package__"] = module_globals.get("__name__", __cubkit_module_id__)
    module_spec = module_globals.get("__spec__")
    if module_spec is not None:
        module_spec.submodule_search_locations = relative_import_paths

__cubkit_bootstrap__()
del __cubkit_bootstrap__'''
