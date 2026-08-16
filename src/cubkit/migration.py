"""Migration helpers for CubKit manifests."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tomllib

from .errors import ManifestError
from .manifest import V1_FIELDS, find_manifest, load_manifest


def migrate_manifest(project_dir: Path) -> tuple[Path, Path | None, bool]:
    """Migrate a legacy manifest to format 2 and keep a ``.bak`` copy."""

    project_dir = project_dir.resolve()
    path = find_manifest(project_dir)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML in {path}: {exc}") from exc

    if data.get("format") == 2:
        load_manifest(project_dir)
        return path, None, False
    if "format" in data or "module" in data or "bundle" in data:
        raise ManifestError("only legacy flat manifests can be migrated")

    unknown = sorted(set(data) - V1_FIELDS - {"libs"})
    if unknown:
        raise ManifestError(
            "refusing migration with unknown legacy fields: " + ", ".join(unknown)
        )
    load_manifest(project_dir)

    backup = path.with_name(path.name + ".bak")
    counter = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.bak.{counter}")
        counter += 1
    shutil.copy2(path, backup)
    path.write_text(_render_format_2(data), encoding="utf-8")
    load_manifest(project_dir)
    return path, backup, True


def _render_format_2(data: dict[str, object]) -> str:
    lines = ["format = 2", "", "[module]"]
    for old, new in (
        ("id", "id"), ("name", "name"), ("version", "version"),
        ("author", "author"), ("description", "description"),
        ("requires", "requires"), ("banner_url", "banner_url"),
        ("scop", "scope"),
    ):
        if old in data:
            lines.append(f"{new} = {_toml_value(data[old])}")

    lines.extend(["", "[bundle]"])
    for old, new in (
        ("src", "source"), ("entrypoint", "entrypoint"), ("out", "output"),
        ("package", "packages"), ("sources", "sources"),
        ("assets", "assets"), ("locales", "locales"), ("sign", "sign"),
    ):
        if old in data:
            lines.append(f"{new} = {_toml_value(data[old])}")

    libs = data.get("libs")
    if isinstance(libs, dict):
        for name, raw_spec in libs.items():
            if not isinstance(name, str) or not isinstance(raw_spec, dict):
                raise ManifestError("invalid legacy libs table")
            lines.extend(["", f"[libs.{name}]"])
            for key, value in raw_spec.items():
                lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ManifestError(f"unsupported manifest value during migration: {value!r}")
