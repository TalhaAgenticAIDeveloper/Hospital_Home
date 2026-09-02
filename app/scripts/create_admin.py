"""
CLI script to create a SaaS Admin account.

Usage:
    python -m app.scripts.create_admin

This script:
1. Prompts for admin email
2. Prompts for password securely (hidden input)
3. Validates email format and password strength
4. Checks for existing admin with same email
5. Creates the admin account with status=ACTIVE
6. Never prints the password

Admin accounts CANNOT be created via the public signup API.
"""

import asyncio
import getpass
import re
import sys

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.database import async_session_maker
from app.core.security import hash_password
from app.models.enums import UserRole, UserStatus
from app.models.user import User

settings = get_settings()


def validate_email(email: str) -> bool:
    """Basic email format validation."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_password(password: str) -> tuple[bool, str]:
    """
    Validate password against the same policy as the signup endpoint.

    Returns (is_valid, error_message).
    """
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        return False, f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters"
    if len(password) > settings.PASSWORD_MAX_LENGTH:
        return False, f"Password must be at most {settings.PASSWORD_MAX_LENGTH} characters"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?`~]", password):
        return False, "Password must contain at least one special character"
    return True, ""


async def create_admin():
    """Interactive admin creation flow."""
    print("=" * 60)
    print("  SaaS Admin Account Creation")
    print("=" * 60)
    print()

    # ── Email ────────────────────────────────────────────────────────
    email = input("Enter admin email: ").strip().lower()
    if not email:
        print("\nError: Email is required.")
        sys.exit(1)
    if not validate_email(email):
        print("\nError: Invalid email format.")
        sys.exit(1)

    # ── Password ─────────────────────────────────────────────────────
    password = getpass.getpass("Enter password: ")
    if not password:
        print("\nError: Password is required.")
        sys.exit(1)

    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("\nError: Passwords do not match.")
        sys.exit(1)

    is_valid, error_msg = validate_password(password)
    if not is_valid:
        print(f"\nError: {error_msg}")
        sys.exit(1)

    # ── Create Admin ─────────────────────────────────────────────────
    async with async_session_maker() as session:
        # Check for existing admin with same email
        stmt = select(User).where(User.email == email)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            print(f"\nError: An account with email '{email}' already exists.")
            sys.exit(1)

        # Create admin user
        admin = User(
            email=email,
            password_hash=hash_password(password),
            role=UserRole.SAAS_ADMIN,
            status=UserStatus.ACTIVE,
            is_active=True,
        )

        try:
            session.add(admin)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            print(f"\nError: An account with email '{email}' already exists.")
            sys.exit(1)

    print()
    print("=" * 60)
    print("  SaaS Admin account created successfully!")
    print(f"  Email: {email}")
    print(f"  Role:  {UserRole.SAAS_ADMIN.value}")
    print("=" * 60)
    print()
    print("You can now login at: POST /api/v1/admin/auth/login")


if __name__ == "__main__":
    asyncio.run(create_admin())
