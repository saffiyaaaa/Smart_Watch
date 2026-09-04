"""Provider-facing exceptions.

The hierarchy exists to answer one question -- retryable or not -- because
that is the only thing call_with_retry needs to know. A timeout or a 5xx might
succeed on a second attempt; a validation failure or an unknown symbol never
will, and retrying it three times just delays the correct response.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for everything a MarketDataProvider may raise."""


class ProviderUnavailable(ProviderError):
    """The provider could not be reached, or returned a server error.

    Retryable.
    """


class RateLimitedError(ProviderUnavailable):
    """The provider asked us to slow down.

    Subclasses ProviderUnavailable so code that only distinguishes retryable
    from non-retryable can treat it identically, while call_with_retry still
    recognises it by name to honour an explicit retry_after instead of the
    default backoff schedule.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderTimeout(ProviderError):
    """The provider did not respond within the configured timeout.

    Retryable.
    """


class InvalidProviderData(ProviderError):
    """The provider responded, but the data cannot be trusted.

    Not retryable: the same malformed response comes back unchanged on the
    next attempt.
    """


class SymbolNotFound(ProviderError):
    """The provider has no data for this symbol -- unknown or delisted.

    Not retryable.
    """
