"""CubKit exception types."""


class CubKitError(Exception):
    """Base error for user-facing CubKit failures."""


class ManifestError(CubKitError):
    """Raised when a module manifest is invalid."""


class BuildError(CubKitError):
    """Raised when a module cannot be built."""
