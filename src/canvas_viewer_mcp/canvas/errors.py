"""Exceptions raised by the Canvas client."""

from __future__ import annotations


class CanvasError(RuntimeError):
    """Base class for all Canvas API failures."""


class CanvasAuthError(CanvasError):
    """The token was rejected (401), or lacks access to the resource (403)."""


class CanvasNotFoundError(CanvasError):
    """The resource does not exist, or the token cannot see it (404)."""


class CanvasRateLimitError(CanvasError):
    """Canvas throttled the request.

    Canvas signals throttling with 403 and a body mentioning the rate limit --
    not the 429 most APIs use -- so it is easy to misread as an auth failure.
    """


class CanvasPaginationError(CanvasError):
    """Pagination did not terminate within the configured page budget.

    Raised rather than returning a partial list: a silent truncation in a tool
    whose purpose is to stop missing coursework would be the worst failure mode
    this project has.
    """
