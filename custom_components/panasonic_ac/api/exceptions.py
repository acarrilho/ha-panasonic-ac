"""Exceptions raised by the Comfort Cloud client."""

from __future__ import annotations


class PanasonicError(Exception):
    """Base class for all client errors."""


class CannotConnect(PanasonicError):
    """Network-level failure or an unexpected server response."""


class InvalidAuth(PanasonicError):
    """Credentials were rejected, or the stored session can't be recovered."""


class MfaRequired(PanasonicError):
    """Login needs a 2FA code. Call ``PanasonicClient.async_verify_mfa(code)``."""

    def __init__(self, message: str = "Panasonic requires a 2FA verification code to sign in") -> None:
        super().__init__(message)


class InvalidMfaCode(PanasonicError):
    """The 2FA code was rejected."""


class AgreementRequired(PanasonicError):
    """Panasonic updated its terms; they must be accepted before the API works."""

    def __init__(self, pending: list[dict] | None = None) -> None:
        self.pending = pending or []
        super().__init__(
            "Panasonic requires updated terms/policies to be accepted "
            "(open the Comfort Cloud app, or enable automatic acceptance)"
        )


class ApiError(PanasonicError):
    """The API answered with an error status."""

    def __init__(self, operation: str, status: int, body: str) -> None:
        self.operation = operation
        self.status = status
        self.body = body
        super().__init__(f"{operation}: HTTP {status}: {body[:300]}")
