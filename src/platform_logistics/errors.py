"""Domain error shared by the pure rules and the store."""


class InvalidInput(ValueError):
    """Client-supplied data failed domain validation (HTTP 422). Any other ValueError is a bug."""
