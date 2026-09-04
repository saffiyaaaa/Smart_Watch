"""Password hashing and JWT issuing.

Uses the `bcrypt` library directly rather than passlib. passlib is an
abstraction over many hashing schemes, and this system has exactly one; the
indirection would buy nothing while adding a dependency that is currently
unmaintained and known to warn against modern bcrypt releases. Per the project
principle, a technology needs a concrete reason to exist.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings

# bcrypt hashes at most 72 bytes and silently ignores the rest. Silent
# truncation is a real vulnerability: two different long passwords sharing a
# 72-byte prefix would become the same credential. We reject over-long input
# instead, so the limit is visible rather than quietly applied.
BCRYPT_MAX_PASSWORD_BYTES = 72

# Cost factor. 12 is roughly 250ms on current hardware -- slow enough to make
# offline cracking expensive, fast enough for an interactive login.
BCRYPT_ROUNDS = 12

# A real bcrypt hash of a value nobody can supply, used to burn the same CPU
# time when an email does not exist. Generated once at import.
_DUMMY_HASH = bcrypt.hashpw(uuid.uuid4().hex.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


class PasswordTooLongError(ValueError):
    def __init__(self) -> None:
        super().__init__(
            f"password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes when UTF-8 encoded"
        )


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_PASSWORD_BYTES:
        raise PasswordTooLongError
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password. Returns False rather than raising on malformed input.

    A corrupted or truncated hash in the database must read as "wrong password",
    not as a 500 -- an error there would tell an attacker something about the
    stored record.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > BCRYPT_MAX_PASSWORD_BYTES:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def burn_password_time() -> None:
    """Spend the same time verifying as a real login would.

    Without this, "unknown email" returns in microseconds while "known email,
    wrong password" takes ~250ms, and the difference lets anyone enumerate which
    addresses are registered. Callers use it on the user-not-found path.
    """
    bcrypt.checkpw(b"invalid", _DUMMY_HASH)


def create_access_token(user_id: uuid.UUID, *, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.jwt_expire_minutes))

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        # Distinguishes an access token from any other token type added later,
        # so a future refresh token cannot be replayed as an access token.
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> uuid.UUID | None:
    """Return the subject, or None if the token is unusable for any reason.

    One return value for every failure mode -- expired, wrong signature,
    malformed, wrong type, unparseable subject. The caller cannot accidentally
    treat "expired" differently from "forged", and the client learns only that
    it must authenticate again.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None

    if payload.get("type") != "access":
        return None

    subject = payload.get("sub")
    if not subject:
        return None

    try:
        return uuid.UUID(subject)
    except (ValueError, AttributeError, TypeError):
        return None
