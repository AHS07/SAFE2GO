"""Authentication and credential utilities.

Covers:
- bcrypt password hashing (cloud schema only)
- JWT session token creation and verification
- Offline credential signing and verification (shift-scoped, short-lived)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import bcrypt
import jwt

from app.config.settings import get_settings
from app.core.clock import wall_clock_now
from app.core.errors import UnauthorizedError

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the plain-text password."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ---------------------------------------------------------------------------
# Session JWT
# ---------------------------------------------------------------------------


def create_access_token(
    user_id: str,
    role: str,
    expires_delta: timedelta | None = None,
    operator_id: str | None = None,
) -> str:
    """Create a signed JWT session token. Operator tokens carry their operator_id."""
    settings = get_settings()
    now = wall_clock_now()
    expire = now + (expires_delta or timedelta(minutes=settings.jwt_expire_minutes))
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": expire,
    }
    if operator_id is not None:
        payload["operator_id"] = operator_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT session token. Raises UnauthorizedError on failure."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid token.") from exc


# ---------------------------------------------------------------------------
# Offline credentials
# ---------------------------------------------------------------------------


def issue_offline_credential(
    shift_id: str,
    operator_id: str,
    machine_id: str,
    pin_hash: str,
    expires_at: datetime,
) -> str:
    """Issue a signed offline credential for a single shift.

    The payload is a JWT signed with the CREDENTIAL_SIGNING_KEY. It holds
    the shift, operator, machine, and a bcrypt verifier for the PIN, never
    the password hash. Expiry is in sim time, so it is a plain claim checked
    by verify_offline_credential rather than the JWT exp claim.
    """
    settings = get_settings()
    payload = {
        "type": "offline_credential",
        "shift_id": shift_id,
        "operator_id": operator_id,
        "machine_id": machine_id,
        "pin_verifier": pin_hash,
        "expires_at": expires_at.isoformat(),
    }
    return jwt.encode(payload, settings.credential_signing_key, algorithm="HS256")


def verify_offline_credential(token: str, machine_id: str, pin: str, now: datetime) -> dict:
    """Verify a shift-scoped offline credential.

    Checks signature, sim-time expiry, machine binding, and PIN.
    Raises UnauthorizedError if any check fails.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.credential_signing_key, algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid offline credential.") from exc

    if payload.get("type") != "offline_credential":
        raise UnauthorizedError("Invalid credential type.")
    expires_at = datetime.fromisoformat(payload["expires_at"])
    if now >= expires_at:
        raise UnauthorizedError("Offline sign-in for this shift has expired.")
    if payload.get("machine_id") != machine_id:
        raise UnauthorizedError("Credential is not valid for this machine.")
    if not verify_password(pin, payload["pin_verifier"]):
        raise UnauthorizedError("Username or PIN is not correct.")
    return payload
