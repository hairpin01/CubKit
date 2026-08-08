"""Download MCUB type stubs/helpers into a CubKit project."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .errors import BuildError

MCUB_TYPES_API_URL = (
    "https://api.github.com/repos/hairpin01/MCUB-fork/contents/core/lib/types?ref=main"
)

UrlOpen = Callable[[str], Any]


def sync_mcub_types(project_dir: Path, *, urlopen: UrlOpen = urllib.request.urlopen) -> list[Path]:
    """Download ``core/lib/types/*.py`` from MCUB and update ``.gitignore``."""

    project_dir = project_dir.resolve()
    types_dir = project_dir / "core" / "lib" / "types"
    types_dir.mkdir(parents=True, exist_ok=True)

    entries = _fetch_json(MCUB_TYPES_API_URL, urlopen=urlopen)
    if not isinstance(entries, list):
        raise BuildError("MCUB types index response is not a list")

    written: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        download_url = entry.get("download_url")
        if not isinstance(name, str) or not name.endswith(".py"):
            continue
        if not isinstance(download_url, str) or not download_url:
            continue
        target = types_dir / name
        target.write_bytes(_fetch_bytes(download_url, urlopen=urlopen))
        written.append(target)

    if not written:
        raise BuildError("No MCUB type files were downloaded")

    _ensure_gitignore_entry(project_dir / ".gitignore", "core/")
    return written


def _fetch_json(url: str, *, urlopen: UrlOpen) -> object:
    try:
        return json.loads(_fetch_bytes(url, urlopen=urlopen).decode("utf-8"))
    except OSError as exc:
        raise BuildError(f"failed to download MCUB types index: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"invalid MCUB types index response: {exc}") from exc


def _fetch_bytes(url: str, *, urlopen: UrlOpen) -> bytes:
    try:
        with urlopen(url) as response:
            return response.read()
    except OSError as exc:
        raise BuildError(f"failed to download {url}: {exc}") from exc


def _ensure_gitignore_entry(path: Path, entry: str) -> None:
    lines: list[str]
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    if entry not in {line.strip() for line in lines}:
        lines.append(entry)
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
