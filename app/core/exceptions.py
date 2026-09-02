"""
Custom exception classes and FastAPI exception handlers.

All user-facing errors return safe messages that do not expose
internal implementation details, SQL queries, or stack traces.
"""

from fastapi import HTTPException, status


class AuthenticationError(HTTPException):
    """401 — Invalid credentials or token."""

    def __init__(self, detail: str = "Invalid email or password"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AuthorizationError(HTTPException):
    """403 — Insufficient permissions or inactive account."""

    def __init__(self, detail: str = "You do not have permission to access this resource"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


class AccountInactiveError(HTTPException):
    """403 — Account is not active (pending, rejected, suspended)."""

    def __init__(self, detail: str = "Account is not active"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


class ConflictError(HTTPException):
    """409 — Resource already exists (e.g., duplicate email)."""

    def __init__(self, detail: str = "A resource with this identifier already exists"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )


class ValidationError(HTTPException):
    """422 — Request validation failed."""

    def __init__(self, detail: str = "Validation error"):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=detail,
        )


class NotFoundError(HTTPException):
    """404 — Resource not found."""

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
        )
