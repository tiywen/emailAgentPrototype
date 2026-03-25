from __future__ import annotations

import base64
import json
from typing import Any, Dict


def peek_access_token_claims(access_token: str) -> Dict[str, Any]:
    """Decode JWT payload without verification (prototype diagnostics only)."""
    token = (access_token or "").strip()
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    pad = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + pad)
        return json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def normalize_jwt_access_token(raw: object) -> str | None:
    """Return a compact JWT string or None if value is empty / not JWS-shaped (3 base64url segments)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("bearer "):
        s = s[7:].strip()
    parts = s.split(".")
    if len(parts) != 3 or not all(parts):
        return None
    if len(s) < 30:
        return None
    return s


def summarize_claims_for_ui(claims: Dict[str, Any]) -> Dict[str, Any]:
    """Strip to non-sensitive fields useful for debugging Graph 401/403."""
    keys = ("aud", "iss", "scp", "roles", "tid", "appid", "iat", "exp", "preferred_username", "upn", "unique_name")
    out: Dict[str, Any] = {}
    for k in keys:
        if k in claims and claims[k] is not None:
            out[k] = claims[k]
    return out
