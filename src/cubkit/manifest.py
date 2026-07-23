"""Manifest loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from .errors import ManifestError

MANIFEST_NAMES = ("cubkit.toml", "mcub.toml")
MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-_]+)?$")


@dataclass(frozen=True)
class Manifest:
    """CubKit module manifest."""

    module_id: str
    name: str
    version: str
    entrypoint: Path
    package: Path | None = None
    assets: Path | None = None


def find_manifest(project_dir: Path) -> Path:
    """Return the manifest path for a module project."""

    for name in MANIFEST_NAMES:
        candidate = project_dir / name
        if candidate.is_file():
            return candidate
    names = " or ".join(MANIFEST_NAMES)
    raise ManifestError(f"manifest not found: expected {names} in {project_dir}")


def load_manifest(project_dir: Path) -> Manifest:
    """Load and validate a module manifest from *project_dir*."""

    project_dir = project_dir.resolve()
    manifest_path = find_manifest(project_dir)
    try:
        data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML in {manifest_path}: {exc}") from exc

    module_id = _required_str(data, "id")
    name = _required_str(data, "name")
    version = _required_str(data, "version")
    entrypoint = _project_path(project_dir, _required_str(data, "entrypoint"), "entrypoint")
    package = _optional_project_path(project_dir, data.get("package"), "package")
    assets = _optional_project_path(project_dir, data.get("assets"), "assets")

    if not MODULE_ID_RE.fullmatch(module_id):
        raise ManifestError("id must match /^[a-z][a-z0-9_]{1,31}$/")
    if not VERSION_RE.fullmatch(version):
        raise ManifestError("version must be semantic, for example 1.0.0")
    if not entrypoint.is_file():
        raise ManifestError(f"entrypoint does not exist or is not a file: {entrypoint}")
    if package is not None and not package.is_dir():
        raise ManifestError(f"package does not exist or is not a directory: {package}")
    if assets is not None and not assets.is_dir():
        raise ManifestError(f"assets does not exist or is not a directory: {assets}")

    _compile_python(entrypoint)
    return Manifest(
        module_id=module_id,
        name=name,
        version=version,
        entrypoint=entrypoint,
        package=package,
        assets=assets,
    )


def _required_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key!r} must be a non-empty string")
    return value.strip()


def _optional_project_path(project_dir: Path, value: object, field: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field!r} must be a non-empty string when provided")
    return _project_path(project_dir, value.strip(), field)


def _project_path(project_dir: Path, value: str, field: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise ManifestError(f"{field!r} must be relative to the module project")
    resolved = (project_dir / path).resolve()
    try:
        resolved.relative_to(project_dir)
    except ValueError as exc:
        raise ManifestError(f"{field!r} must stay inside the module project") from exc
    return resolved


def _compile_python(path: Path) -> None:
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as exc:
        raise ManifestError(f"syntax error in {path}: {exc.msg} at line {exc.lineno}") from exc
