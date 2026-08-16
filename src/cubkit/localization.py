"""Load project-local strings in MCUB's native locale mapping format."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib
from typing import Any

import yaml

from .errors import ManifestError

LOCALE_SUFFIXES = {".json", ".toml", ".yaml", ".yml"}
LOCALE_RE = re.compile(r"^[a-z]{2,3}$")


def load_locales(directory: Path | None) -> dict[str, dict[str, Any]]:
    """Return ``{locale: strings}`` ready for ``ModuleBase.strings``."""

    if directory is None:
        return {}

    locales: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in LOCALE_SUFFIXES:
            continue

        locale = path.stem
        if not LOCALE_RE.fullmatch(locale):
            raise ManifestError(
                f"locale filename must be a lowercase language code like en, ru or uk: {path}"
            )
        if locale in locales:
            raise ManifestError(f"duplicate locale {locale!r}: {path}")

        data = _load_locale_file(path)
        if not isinstance(data, dict):
            raise ManifestError(f"locale file must contain a mapping: {path}")
        if not data:
            raise ManifestError(f"locale file must not be empty: {path}")
        _validate_locale_values(data, path)
        locales[locale] = data

    if not locales:
        suffixes = ", ".join(sorted(LOCALE_SUFFIXES))
        raise ManifestError(
            f"locales directory contains no supported files ({suffixes}): {directory}"
        )
    return locales


def _load_locale_file(path: Path) -> object:
    try:
        text = path.read_text(encoding="utf-8")
        suffix = path.suffix.lower()
        if suffix == ".json":
            return json.loads(text)
        if suffix == ".toml":
            return tomllib.loads(text)
        return yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"invalid locale file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML locale file {path}: {exc}") from exc


def _validate_locale_values(
    data: dict[object, object], path: Path, prefix: str = ""
) -> None:
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            raise ManifestError(f"locale keys must be non-empty strings: {path}")
        qualified = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            _validate_locale_values(value, path, qualified)
        elif not isinstance(value, str):
            raise ManifestError(
                f"locale value for {qualified!r} must be a string: {path}"
            )
