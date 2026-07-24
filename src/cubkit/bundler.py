"""Create deterministic embedded bundles."""

from __future__ import annotations

import io
import zipfile

from .collector import BundleFile

ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def build_zip_payload(files: list[BundleFile]) -> bytes:
    """Return deterministic zip bytes for *files*."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(
        buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for item in files:
            info = zipfile.ZipInfo(item.archive_name, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, item.source.read_bytes())
    return buffer.getvalue()
