"""Collect vendored libraries for ``cubkit.lib`` imports."""

from __future__ import annotations

import importlib.machinery
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .errors import BuildError
from .manifest import LibrarySpec, Manifest

LIB_ARCHIVE_ROOT = "_cubkit_lib"
DependencyProgress = Callable[[str, int, int], None]


@dataclass(frozen=True)
class LibraryFile:
    """A vendored library file that will be embedded into the artifact."""

    source: Path
    archive_name: str
    data: bytes | None = None
    display_name: str | None = None


def collect_library_files(
    manifest: Manifest, progress: DependencyProgress | None = None
) -> list[LibraryFile]:
    """Collect vendored libraries declared in ``manifest.libs``."""

    files: list[LibraryFile] = []
    total = len(manifest.libs)
    for index, library in enumerate(manifest.libs, start=1):
        if library.type == "local":
            if library.path is None:
                raise BuildError(f"libs.{library.name}: local library requires a path")
            files.extend(_collect_local_library(library))
        elif library.type in {"url", "package_pip", "package_github"}:
            files.extend(_collect_pip_library(library))
        else:
            raise BuildError(f"libs.{library.name}: unsupported library type {library.type!r}")
        if progress is not None:
            progress(library.name, index, total)
    return sorted(_dedupe(files), key=lambda item: item.archive_name)


def _collect_local_library(library: LibrarySpec) -> list[LibraryFile]:
    assert library.path is not None
    path = library.path
    if path.is_file():
        if path.suffix == ".whl":
            return _collect_wheel_library(library, path)
        if path.suffix == ".py":
            archive_name = PurePosixPath(LIB_ARCHIVE_ROOT, f"{library.name}.py").as_posix()
            return [LibraryFile(source=path, archive_name=archive_name)]
        extension_suffix = _extension_suffix(path)
        if extension_suffix is not None:
            archive_name = PurePosixPath(
                LIB_ARCHIVE_ROOT, f"{library.name}{extension_suffix}"
            ).as_posix()
            return [LibraryFile(source=path, archive_name=archive_name)]
        raise BuildError(
            f"libs.{library.name}: local file libraries must be .py, .whl, or Python native extension files"
        )

    if _is_installable_project(path):
        return _collect_pip_requirement(library, str(path))

    package_root = _find_import_root(path, library.name)
    if package_root is None:
        raise BuildError(
            f"libs.{library.name}: could not find import package. Expected one of: "
            f"{path / (library.name + '.py')}, {path / library.name / '__init__.py'}, "
            f"{path / 'src' / library.name / '__init__.py'}, or {path / '__init__.py'}"
        )

    source_root, archive_root = package_root
    return _collect_tree(source_root, archive_root)


def _find_import_root(path: Path, name: str) -> tuple[Path, PurePosixPath] | None:
    module_file = path / f"{name}.py"
    if module_file.is_file():
        return module_file, PurePosixPath(LIB_ARCHIVE_ROOT, f"{name}.py")

    for candidate in (path / name, path / "src" / name, path):
        if (candidate / "__init__.py").is_file():
            return candidate, PurePosixPath(LIB_ARCHIVE_ROOT, name)
    return None


def _is_installable_project(path: Path) -> bool:
    return any((path / filename).is_file() for filename in ("pyproject.toml", "setup.py", "setup.cfg"))


def _collect_wheel_library(library: LibrarySpec, path: Path) -> list[LibraryFile]:
    files: list[LibraryFile] = []
    with zipfile.ZipFile(path) as wheel:
        for info in sorted(wheel.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            member = PurePosixPath(info.filename)
            if not _is_safe_wheel_member(member):
                continue
            if not _belongs_to_library(member, library.name):
                continue
            files.append(
                LibraryFile(
                    source=path,
                    archive_name=PurePosixPath(LIB_ARCHIVE_ROOT, member).as_posix(),
                    data=wheel.read(info),
                    display_name=f"{path.name}:{member.as_posix()}",
                )
            )

    if not files:
        raise BuildError(
            f"libs.{library.name}: wheel does not contain import package {library.name!r}"
        )
    return files


def _is_safe_wheel_member(path: PurePosixPath) -> bool:
    return not path.is_absolute() and ".." not in path.parts


def _belongs_to_library(path: PurePosixPath, name: str) -> bool:
    first = path.parts[0] if path.parts else ""
    return first == name or first == f"{name}.py" or first.startswith(f"{name}.")


def _extension_suffix(path: Path) -> str | None:
    filename = path.name
    for suffix in importlib.machinery.EXTENSION_SUFFIXES:
        if filename.endswith(suffix):
            return suffix
    if path.suffix in {".so", ".pyd", ".dll", ".dylib"}:
        return path.suffix
    return None


def _collect_pip_library(library: LibrarySpec) -> list[LibraryFile]:
    requirement = _pip_requirement(library)
    return _collect_pip_requirement(library, requirement)


def _collect_pip_requirement(library: LibrarySpec, requirement: str) -> list[LibraryFile]:
    with tempfile.TemporaryDirectory(prefix=f"cubkit-lib-{library.name}-") as tmp:
        target = Path(tmp) / "site"
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-input",
            "--disable-pip-version-check",
            "--no-compile",
            "--target",
            str(target),
            requirement,
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BuildError(
                f"libs.{library.name}: pip install failed for {requirement!r}\n"
                f"{result.stdout.strip()}"
            )
        files = _collect_tree(target, PurePosixPath(LIB_ARCHIVE_ROOT), inline_data=True)
    if not any(_belongs_to_library(PurePosixPath(item.archive_name).relative_to(LIB_ARCHIVE_ROOT), library.name) for item in files):
        raise BuildError(
            f"libs.{library.name}: installed package does not contain import package {library.name!r}"
        )
    return files


def _pip_requirement(library: LibrarySpec) -> str:
    if library.type == "url" and library.url:
        return library.url
    if library.type == "package_pip" and library.package_pip:
        return library.package_pip
    if library.type == "package_github" and library.package_github:
        return _github_requirement(library.package_github)
    raise BuildError(f"libs.{library.name}: missing dependency descriptor for type {library.type!r}")


def _github_requirement(value: str) -> str:
    value = value.strip()
    if value.startswith("git+") or value.startswith("file:"):
        return value
    if value.startswith("git@github.com:"):
        owner_repo = value.removeprefix("git@github.com:")
        return f"git+ssh://git@github.com/{owner_repo}"
    if value.startswith("ssh://git@github.com/"):
        return f"git+{value}"
    if value.startswith("https://github.com/") or value.startswith("http://github.com/"):
        return f"git+{value}"
    if value.count("/") == 1 and not value.startswith(("/", ".")):
        return f"git+https://github.com/{value}"
    return value


def _collect_tree(source: Path, archive_root: PurePosixPath, *, inline_data: bool = False) -> list[LibraryFile]:
    if source.is_file():
        return [
            LibraryFile(
                source=source,
                archive_name=archive_root.as_posix(),
                data=source.read_bytes() if inline_data else None,
                display_name=source.as_posix() if inline_data else None,
            )
        ]

    files: list[LibraryFile] = []
    for file_path in source.rglob("*"):
        if not file_path.is_file() or _is_excluded(file_path):
            continue
        relative = PurePosixPath(file_path.relative_to(source).as_posix())
        files.append(
            LibraryFile(
                source=file_path,
                archive_name=(archive_root / relative).as_posix(),
                data=file_path.read_bytes() if inline_data else None,
                display_name=file_path.as_posix() if inline_data else None,
            )
        )
    return files


def _is_excluded(path: Path) -> bool:
    return (
        "__pycache__" in path.parts
        or path.suffix in {".pyc", ".pyo"}
        or path.name == "INSTALLER"
        or path.name == "RECORD"
        or path.name == "REQUESTED"
    )


def _dedupe(files: list[LibraryFile]) -> list[LibraryFile]:
    result: dict[str, LibraryFile] = {}
    for item in files:
        result.setdefault(item.archive_name, item)
    return list(result.values())
