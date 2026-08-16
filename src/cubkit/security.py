"""Checks that prevent accidental bundling of credentials."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .collector import BundleFile
from .errors import BuildError

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|bot[_-]?token|session[_-]?string)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./:+-]{16,}"),
    re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{20,}\b"),  # Telegram bot token
)


def validate_no_secrets(files: Iterable[BundleFile]) -> None:
    """Raise a concise error when a bundled text file resembles a credential."""

    findings: list[str] = []
    for item in files:
        try:
            text = item.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _SECRET_PATTERNS):
                findings.append(f"{item.archive_name}:{line_number}")
                break
    if findings:
        raise BuildError("possible secret in bundle: " + ", ".join(findings))
