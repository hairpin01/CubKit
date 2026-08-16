"""CubKit public package."""

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

__all__ = [
    "__version__",
    "assets",
    "get_environment",
    "load_strings",
    "metadata",
    "resource",
    "root",
]

__version__ = "0.1.3"
assets: Any = None
metadata: Mapping[str, Any] = MappingProxyType({})
root: Path | None = None


def _runtime_only(name: str) -> RuntimeError:
    return RuntimeError(f"{name} is available only in a built CubKit module")


def get_environment() -> Mapping[str, Any]:
    """Return the generated module environment at runtime."""

    raise _runtime_only("get_environment()")


def load_strings() -> dict[str, dict[str, Any]]:
    """Return project locales inside a generated CubKit artifact.

    The generated bootstrap replaces this development-time stub with a
    module-bound implementation before the entrypoint is executed.
    """

    raise _runtime_only("load_strings()")


def resource(relative_path: str | Path) -> Path:
    """Resolve a bundled asset path inside a generated module."""

    del relative_path
    raise _runtime_only("resource()")
