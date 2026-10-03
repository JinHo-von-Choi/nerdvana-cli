"""Exceptions of the app."""


class ValidationError(Exception):
    """A record broke one or more rules."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class NotFound(Exception):
    """A record does not exist."""
