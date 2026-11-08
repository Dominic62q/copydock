class CopyDockError(Exception):
    """An expected, user-facing failure. The message must say what to do next."""


class UnsupportedComposeFeature(CopyDockError):
    """The Compose file uses something v1 deliberately rejects (see V1.md, Limits)."""
