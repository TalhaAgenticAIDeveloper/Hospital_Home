"""
Security utilities: password hashing (Argon2id) and JWT token management.

NEVER log passwords, password hashes, or JWT tokens.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()

# ── Password Hashing (Argon2id) ─────────────────────────────────────────────
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    argon2__memory_cost=65536,   # 64 MiB
    argon2__time_cost=3,         # 3 iterations
    argon2__parallelism=4,       # 4 threads
)


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT Token Management ────────────────────────────────────────────────────

def create_access_token(
    sub: str,
    role: str,
    extra_claims: dict | None = None,
) -> str:
    """
    Create a short-lived JWT access token.

    Args:
        sub: Subject (user ID as string).
        role: User role.
        extra_claims: Optional additional claims.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(sub: str, role: str) -> str:
    """
    Create a longer-lived JWT refresh token.

    Args:
        sub: Subject (user ID as string).
        role: User role.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "role": role,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "jti": str(uuid.uuid4()),
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    """
    Decode and validate a JWT token.

    Args:
        token: Encoded JWT string.

    Returns:
        Decoded payload dict.

    Raises:
        JWTError: If token is invalid, expired, or has a bad signature.
    """
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )


def hash_token(token: str) -> str:
    """
    Create a SHA-256 hash of a token for secure database storage.

    We never store raw refresh tokens in the database. Instead, we store
    a hash so that even if the database is compromised, tokens cannot
    be replayed.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
