"""Password hashing and token handling."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.infrastructure.security import (
    BCRYPT_MAX_PASSWORD_BYTES,
    PasswordTooLongError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_then_verify(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", h) is True

    def test_wrong_password_rejected(self):
        h = hash_password("correct horse battery staple")
        assert verify_password("wrong password", h) is False

    def test_hash_is_salted(self):
        """Two identical passwords must not produce identical hashes, or a
        leaked table would reveal which users share a password."""
        assert hash_password("same") != hash_password("same")

    def test_hash_is_not_the_plaintext(self):
        assert "hunter2" not in hash_password("hunter2")

    def test_malformed_hash_reads_as_wrong_password(self):
        """A corrupted stored hash must not become a 500 -- an error there
        tells an attacker something about the record."""
        assert verify_password("anything", "not-a-bcrypt-hash") is False
        assert verify_password("anything", "") is False

    def test_over_long_password_rejected_not_truncated(self):
        """Silent truncation is a real vulnerability: two different long
        passwords sharing a 72-byte prefix would become one credential."""
        with pytest.raises(PasswordTooLongError):
            hash_password("x" * (BCRYPT_MAX_PASSWORD_BYTES + 1))

    def test_boundary_length_accepted(self):
        pw = "x" * BCRYPT_MAX_PASSWORD_BYTES
        assert verify_password(pw, hash_password(pw)) is True

    def test_multibyte_password_measured_in_bytes(self):
        """An emoji is four UTF-8 bytes, so 19 of them exceed the limit even
        though the string is only 19 characters long."""
        pw = "\U0001f600" * 19  # 76 bytes
        assert len(pw) < BCRYPT_MAX_PASSWORD_BYTES
        with pytest.raises(PasswordTooLongError):
            hash_password(pw)

    def test_unicode_password_round_trips(self):
        pw = "pässwörd-üñî"
        assert verify_password(pw, hash_password(pw)) is True


class TestAccessTokens:
    def test_round_trip(self):
        uid = uuid.uuid4()
        assert decode_access_token(create_access_token(uid)) == uid

    def test_expired_token_rejected(self):
        token = create_access_token(uuid.uuid4(), expires_delta=timedelta(seconds=-10))
        assert decode_access_token(token) is None

    def test_tampered_token_rejected(self):
        token = create_access_token(uuid.uuid4())
        assert decode_access_token(token[:-4] + "AAAA") is None

    def test_garbage_rejected(self):
        for junk in ("", "not.a.token", "a.b.c", "Bearer xyz"):
            assert decode_access_token(junk) is None

    def test_token_signed_with_another_secret_rejected(self):
        from jose import jwt

        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "access", "exp": 9999999999},
            "attacker-chosen-secret",
            algorithm="HS256",
        )
        assert decode_access_token(forged) is None

    def test_unsigned_token_rejected(self):
        """The alg=none attack: a token declaring it needs no signature.

        Hand-assembled, because python-jose refuses to *create* one -- which is
        itself reassuring, but does not prove our decode path rejects a token
        an attacker crafted by hand. That is the case that matters.
        """
        import base64
        import json

        def b64(data: dict) -> str:
            raw = json.dumps(data, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        header = b64({"alg": "none", "typ": "JWT"})
        payload = b64({"sub": str(uuid.uuid4()), "type": "access", "exp": 9999999999})
        forged = f"{header}.{payload}."

        assert decode_access_token(forged) is None

    def test_wrong_token_type_rejected(self):
        """A future refresh token must not be replayable as an access token."""
        from jose import jwt

        from app.config import get_settings

        s = get_settings()
        other = jwt.encode(
            {"sub": str(uuid.uuid4()), "type": "refresh", "exp": 9999999999},
            s.jwt_secret,
            algorithm=s.jwt_algorithm,
        )
        assert decode_access_token(other) is None

    def test_non_uuid_subject_rejected(self):
        from jose import jwt

        from app.config import get_settings

        s = get_settings()
        bad = jwt.encode(
            {"sub": "not-a-uuid", "type": "access", "exp": 9999999999},
            s.jwt_secret,
            algorithm=s.jwt_algorithm,
        )
        assert decode_access_token(bad) is None
