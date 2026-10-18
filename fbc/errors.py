"""Shared error types."""


class ResumeLater(RuntimeError):
    """A rate limit or quota stop: keep everything done so far and continue on the next tick."""
