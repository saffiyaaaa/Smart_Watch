"""Authentication request and response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.infrastructure.security import BCRYPT_MAX_PASSWORD_BYTES

MIN_PASSWORD_LENGTH = 8


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)

    @field_validator("password")
    @classmethod
    def _within_bcrypt_limit(cls, v: str) -> str:
        """Reject over-long passwords rather than let bcrypt truncate silently.

        Measured in UTF-8 bytes, not characters: an emoji is four bytes, so a
        58-character password can exceed the 72-byte limit. Validating on
        len(str) would pass it here and then fail deeper in the stack.
        """
        if len(v.encode("utf-8")) > BCRYPT_MAX_PASSWORD_BYTES:
            raise ValueError(
                f"password must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes when UTF-8 encoded"
            )
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds")


class UserResponse(BaseModel):
    # Reads attributes off the SQLAlchemy object. password_hash is absent from
    # this model, so it cannot be serialised into a response by accident -- the
    # schema is an allowlist, not a filter applied afterwards.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime
