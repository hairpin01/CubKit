"""Manifest loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from .errors import ManifestError
from .localization import load_locales

MANIFEST_NAMES = ("cubkit.toml", "mcub.toml")
MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
V1_FIELDS = {
    "id", "name", "version", "author", "description", "src", "entrypoint",
    "out", "package", "assets", "locales", "sources", "requires",
    "banner_url", "scop", "sign", "debug_output", "release_output", "include",
    "exclude", "fail_on_secrets",
}
V2_MODULE_FIELDS = {
    "id", "name", "version", "author", "description", "requires",
    "banner_url", "scope",
}
V2_BUNDLE_FIELDS = {
    "source", "entrypoint", "output", "packages", "sources", "assets",
    "locales", "sign", "debug_output", "release_output", "include", "exclude",
    "fail_on_secrets",
}


@dataclass(frozen=True)
class LibrarySpec:
    """A vendored library requested by the manifest."""

    name: str
    type: str
    path: Path | None = None
    url: str | None = None
    package_pip: str | None = None
    package_github: str | None = None


@dataclass(frozen=True)
class Hooks:
    """Commands run around CubKit operations."""

    common: dict[str, tuple[tuple[str, ...], ...]]
    profiles: dict[str, dict[str, tuple[tuple[str, ...], ...]]]


@dataclass(frozen=True)
class Manifest:
    """CubKit module manifest."""

    module_id: str
    name: str
    project_dir: Path
    source_root: Path
    entrypoint: Path
    format_version: int = 1
    out: Path | None = None
    version: str | None = None
    author: str = "unknown"
    description: str = ""
    package: tuple[Path, ...] = ()
    assets: Path | None = None
    locales: Path | None = None
    sources: tuple[Path, ...] = ()
    libs: tuple[LibrarySpec, ...] = ()
    requires: tuple[str, ...] = ()
    banner_url: str | None = None
    scop: str | None = None
    sign: bool = False
    debug_out: Path | None = None
    release_out: Path | None = None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    fail_on_secrets: bool = False
    hooks: Hooks | None = None


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
        raw_data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML in {manifest_path}: {exc}") from exc

    data, format_version = _normalize_manifest_data(raw_data)
    module_id = _required_str(data, "id")
    name = _optional_str(data, "name", default=module_id)
    version = _optional_str(data, "version", default=None)
    author = _optional_str(data, "author", default="unknown")
    description = _optional_str(data, "description", default="")
    source_root = _optional_project_path(project_dir, data.get("src"), "src") or project_dir
    entrypoint = _project_path(
        source_root, _required_str(data, "entrypoint"), "entrypoint"
    )
    out = _optional_project_path(project_dir, data.get("out"), "out")
    package = _optional_project_paths(source_root, data.get("package"), "package")
    assets = _optional_project_path(project_dir, data.get("assets"), "assets")
    locales = _optional_project_path(project_dir, data.get("locales"), "locales")
    sources = _optional_project_paths(source_root, data.get("sources"), "sources")
    libs = _optional_libraries(project_dir, data.get("libs"))
    requires = _optional_str_tuple(data, "requires")
    banner_url = _optional_str(data, "banner_url", default=None)
    scop = _optional_str(data, "scop", default=None)
    sign = _optional_bool(data, "sign", default=False)
    debug_out = _optional_project_path(project_dir, data.get("debug_output"), "debug_output")
    release_out = _optional_project_path(project_dir, data.get("release_output"), "release_output")
    include = _optional_str_tuple(data, "include")
    exclude = _optional_str_tuple(data, "exclude")
    fail_on_secrets = _optional_bool(data, "fail_on_secrets", default=False)
    hooks = _optional_hooks(data.get("hooks"))

    if not MODULE_ID_RE.fullmatch(module_id):
        raise ManifestError("id must match /^[a-z][a-z0-9_]{1,31}$/")
    if not source_root.is_dir():
        raise ManifestError(f"src does not exist or is not a directory: {source_root}")
    if not entrypoint.is_file():
        raise ManifestError(f"entrypoint does not exist or is not a file: {entrypoint}")
    if out is not None and out.suffix != ".py":
        raise ManifestError("out must be a .py file")
    for output_name, output_path in (("debug_output", debug_out), ("release_output", release_out)):
        if output_path is not None and output_path.suffix != ".py":
            raise ManifestError(f"{output_name} must be a .py file")
    for package_path in package:
        if not package_path.is_dir():
            raise ManifestError(
                f"package does not exist or is not a directory: {package_path}"
            )
    if assets is not None and not assets.is_dir():
        raise ManifestError(f"assets does not exist or is not a directory: {assets}")
    if locales is not None:
        if not locales.is_dir():
            raise ManifestError(
                f"locales does not exist or is not a directory: {locales}"
            )
        load_locales(locales)

    _compile_python(entrypoint)
    return Manifest(
        module_id=module_id,
        name=name,
        project_dir=project_dir,
        source_root=source_root,
        out=out,
        format_version=format_version,
        version=version,
        author=author,
        description=description,
        entrypoint=entrypoint,
        package=package,
        assets=assets,
        locales=locales,
        sources=sources,
        libs=libs,
        requires=requires,
        banner_url=banner_url,
        scop=scop,
        sign=sign,
        debug_out=debug_out,
        release_out=release_out,
        include=include,
        exclude=exclude,
        fail_on_secrets=fail_on_secrets,
        hooks=hooks,
    )


def _normalize_manifest_data(data: dict[str, object]) -> tuple[dict[str, object], int]:
    """Normalize legacy and format-2 manifests to the internal flat schema."""

    has_v2_sections = "module" in data or "bundle" in data
    if not has_v2_sections and "format" not in data:
        return data, 1

    format_value = data.get("format")
    if type(format_value) is not int or format_value != 2:
        raise ManifestError("structured manifests must declare format = 2")

    mixed = sorted(V1_FIELDS.intersection(data))
    if mixed:
        raise ManifestError(
            "format 2 cannot mix legacy root fields: " + ", ".join(mixed)
        )
    unknown_root = sorted(set(data) - {"format", "module", "bundle", "libs", "hooks"})
    if unknown_root:
        raise ManifestError("unknown format 2 fields: " + ", ".join(unknown_root))

    module = _required_table(data, "module")
    bundle = _required_table(data, "bundle")
    _reject_unknown_table_fields(module, V2_MODULE_FIELDS, "module")
    _reject_unknown_table_fields(bundle, V2_BUNDLE_FIELDS, "bundle")

    normalized: dict[str, object] = {}
    module_map = {
        "id": "id", "name": "name", "version": "version",
        "author": "author", "description": "description", "requires": "requires",
        "banner_url": "banner_url", "scope": "scop",
    }
    bundle_map = {
        "source": "src", "entrypoint": "entrypoint", "output": "out",
        "packages": "package", "sources": "sources", "assets": "assets",
        "locales": "locales", "sign": "sign",
        "debug_output": "debug_output", "release_output": "release_output",
        "include": "include", "exclude": "exclude",
        "fail_on_secrets": "fail_on_secrets",
    }
    for source, target in module_map.items():
        if source in module:
            normalized[target] = module[source]
    for source, target in bundle_map.items():
        if source in bundle:
            normalized[target] = bundle[source]
    if "libs" in data:
        normalized["libs"] = data["libs"]
    if "hooks" in data:
        normalized["hooks"] = data["hooks"]
    return normalized, 2


def _required_table(data: dict[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ManifestError(f"format 2 requires a [{key}] table")
    return value


def _reject_unknown_table_fields(
    data: dict[str, object], allowed: set[str], table: str
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ManifestError(f"unknown [{table}] fields: {', '.join(unknown)}")


def _required_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key!r} must be a non-empty string")
    return value.strip()


def _optional_str(
    data: dict[str, object], key: str, *, default: str | None
) -> str | None:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key!r} must be a non-empty string when provided")
    return value.strip()


def _optional_str_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = []
        for item in value:
            if not isinstance(item, str):
                raise ManifestError(f"{key!r} must contain only strings")
            items.append(item.strip())
    else:
        raise ManifestError(f"{key!r} must be a string or list of strings")
    if any(not item for item in items):
        raise ManifestError(f"{key!r} must not contain empty values")
    return tuple(items)


def _optional_bool(data: dict[str, object], key: str, *, default: bool) -> bool:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ManifestError(f"{key!r} must be true or false when provided")
    return value


def _optional_hooks(value: object) -> Hooks | None:
    """Validate hooks while keeping command execution separate from parsing."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ManifestError("'hooks' must be a table")

    common: dict[str, tuple[tuple[str, ...], ...]] = {}
    profiles: dict[str, dict[str, tuple[tuple[str, ...], ...]]] = {}
    allowed_events = {"pre_check", "post_check", "pre_build", "post_build"}
    for name, commands in value.items():
        if name in {"debug", "release"}:
            if not isinstance(commands, dict):
                raise ManifestError(f"[hooks.{name}] must be a table")
            profiles[name] = _parse_hook_events(commands, allowed_events, f"hooks.{name}")
        elif name in allowed_events:
            common[name] = _parse_hook_commands(commands, f"hooks.{name}")
        else:
            raise ManifestError(f"unknown [hooks] field: {name}")
    return Hooks(common=common, profiles=profiles)


def _parse_hook_events(
    values: dict[str, object], allowed: set[str], table: str
) -> dict[str, tuple[tuple[str, ...], ...]]:
    events: dict[str, tuple[tuple[str, ...], ...]] = {}
    for event, commands in values.items():
        if event not in allowed:
            raise ManifestError(f"unknown [{table}] field: {event}")
        events[event] = _parse_hook_commands(commands, f"{table}.{event}")
    return events


def _parse_hook_commands(value: object, key: str) -> tuple[tuple[str, ...], ...]:
    """Parse an argv list or a list of argv lists without invoking a shell."""

    if not isinstance(value, list) or not value:
        raise ManifestError(f"{key!r} must be a non-empty list of command arguments")
    if all(isinstance(item, str) and item for item in value):
        return (tuple(value),)
    commands: list[tuple[str, ...]] = []
    for command in value:
        if not isinstance(command, list) or not command or not all(
            isinstance(arg, str) and arg for arg in command
        ):
            raise ManifestError(
                f"{key!r} must be an argv list or a list of non-empty argv lists"
            )
        commands.append(tuple(command))
    return tuple(commands)


def _optional_libraries(project_dir: Path, value: object) -> tuple[LibrarySpec, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ManifestError("'libs' must be a table of library definitions")

    libraries: list[LibrarySpec] = []
    for raw_name, raw_spec in value.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ManifestError("library names in 'libs' must be non-empty strings")
        name = raw_name.strip()
        if not name.isidentifier():
            raise ManifestError(f"library name must be a valid Python identifier: {name!r}")
        if not isinstance(raw_spec, dict):
            raise ManifestError(f"libs.{name!s} must be a table")

        lib_type = _library_type(raw_spec)
        path_value = _library_str(raw_spec, "path")
        path = _library_path(project_dir, path_value) if path_value else None
        if lib_type == "local":
            if path is None:
                raise ManifestError(f"libs.{name}.path is required for local libraries")
            if not path.exists():
                raise ManifestError(f"library path does not exist: {path}")

        libraries.append(
            LibrarySpec(
                name=name,
                type=lib_type,
                path=path,
                url=_library_str(raw_spec, "url"),
                package_pip=_library_str(raw_spec, "package_pip"),
                package_github=_library_str(raw_spec, "package_github"),
            )
        )
    return tuple(libraries)


def _library_str(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"library field {key!r} must be a non-empty string when provided")
    return value.strip()


def _library_type(data: dict[str, object]) -> str:
    explicit = _library_str(data, "type")
    if explicit:
        return explicit
    if _library_str(data, "path"):
        return "local"
    if _library_str(data, "url"):
        return "url"
    if _library_str(data, "package_pip"):
        return "package_pip"
    if _library_str(data, "package_github"):
        return "package_github"
    return "local"


def _library_path(project_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (project_dir / path).resolve()


def _optional_project_path(project_dir: Path, value: object, field: str) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field!r} must be a non-empty string when provided")
    return _project_path(project_dir, value.strip(), field)


def _optional_project_paths(
    project_dir: Path, value: object, field: str
) -> tuple[Path, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ManifestError(f"{field!r} must be a string or list of strings")

    paths: list[Path] = []
    for item in raw_items:
        if not isinstance(item, str) or not item.strip():
            raise ManifestError(f"{field!r} must contain non-empty strings")
        path = _project_path(project_dir, item.strip(), field)
        if not path.exists():
            raise ManifestError(f"{field} path does not exist: {path}")
        paths.append(path)
    return tuple(paths)


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
        raise ManifestError(
            f"syntax error in {path}: {exc.msg} at line {exc.lineno}"
        ) from exc
