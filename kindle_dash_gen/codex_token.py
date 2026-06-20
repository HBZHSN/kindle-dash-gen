from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any


EXPIRY_WARNING_SECONDS = 24 * 60 * 60


def normalize_token(value: object) -> str:
    """Return a bare bearer token suitable for storing in config."""
    if value is None:
        token = ""
    elif isinstance(value, str):
        token = value.strip()
    else:
        raise ValueError("Token must be a string")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if any(char.isspace() for char in token):
        raise ValueError("Token must not contain whitespace")
    if len(token) > 16_384:
        raise ValueError("Token is too long")
    return token


def _jwt_payload(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _timestamp(payload: dict[str, Any] | None, key: str) -> int | None:
    if not payload:
        return None
    try:
        value = int(float(payload[key]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    try:
        datetime.fromtimestamp(value, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None
    return value


def inspect_token(token: object, now: datetime | None = None) -> dict[str, Any]:
    """Decode JWT time claims without verifying or exposing other claims."""
    normalized = normalize_token(token)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current_ts = int(current.timestamp())

    if not normalized:
        return {
            "configured": False,
            "expires_at": None,
            "expires_in_seconds": None,
            "issued_at": None,
            "status": "missing",
            "expiring_soon": False,
            "expired": False,
        }

    payload = _jwt_payload(normalized)
    expires_at = _timestamp(payload, "exp")
    issued_at = _timestamp(payload, "iat")
    if expires_at is None:
        status = "unknown"
        expires_in = None
    else:
        expires_in = expires_at - current_ts
        if expires_in <= 0:
            status = "expired"
        elif expires_in <= EXPIRY_WARNING_SECONDS:
            status = "expiring"
        else:
            status = "valid"

    return {
        "configured": True,
        "expires_at": expires_at,
        "expires_in_seconds": expires_in,
        "issued_at": issued_at,
        "status": status,
        "expiring_soon": status == "expiring",
        "expired": status == "expired",
    }
