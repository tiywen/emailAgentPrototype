from __future__ import annotations

import os
import unicodedata
from typing import Any, Dict, List

import msal
from msal import SerializableTokenCache
from msal.authority import AZURE_PUBLIC, AuthorityBuilder

# Microsoft Graph delegated scopes (v2 resource URI prefix)
# Mail.ReadWrite is required for future draft creation.
GRAPH_SCOPES = ["https://graph.microsoft.com/User.Read", "https://graph.microsoft.com/Mail.ReadWrite"]


def _clean_id(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().strip('"').strip("'")
    # Remove BOM / invisible formatting chars sometimes pasted from portals
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc"))
    return text if text else None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _authority_override() -> str | None:
    return _clean_id(os.getenv("AZURE_AUTHORITY"))


def get_entra_env() -> tuple[str, str | None]:
    """Return (client_id, tenant_id). Tenant is optional when ``AZURE_AUTHORITY`` is set (e.g. .../common)."""
    client_id = _clean_id(os.getenv("AZURE_CLIENT_ID")) or _clean_id(os.getenv("MICROSOFT_CLIENT_ID"))
    tenant_id = _clean_id(os.getenv("AZURE_TENANT_ID")) or _clean_id(os.getenv("MICROSOFT_TENANT_ID"))
    if not client_id:
        raise ValueError(
            "Set AZURE_CLIENT_ID (or MICROSOFT_CLIENT_ID) in the environment or project `.env`."
        )
    auth_override = _authority_override()
    if auth_override:
        if tenant_id and client_id == tenant_id:
            raise ValueError(
                "AZURE_CLIENT_ID and AZURE_TENANT_ID are identical — use Application (client) ID vs Directory (tenant) ID."
            )
        return client_id, tenant_id
    if not tenant_id:
        raise ValueError(
            "Set AZURE_TENANT_ID, or set AZURE_AUTHORITY=https://login.microsoftonline.com/common "
            "when signing in with personal Microsoft accounts (MSA) for mail."
        )
    if client_id == tenant_id:
        raise ValueError(
            "AZURE_CLIENT_ID and AZURE_TENANT_ID are identical — you likely pasted the Application (client) ID twice. "
            "Tenant ID is a different GUID from **Identity > Overview > Directory (tenant) ID**."
        )
    return client_id, tenant_id


def build_public_client(token_cache: msal.TokenCache | None = None) -> msal.PublicClientApplication:
    """Build PCA. Pass ``token_cache`` (e.g. ``SerializableTokenCache``) to persist tokens across Streamlit reruns."""
    client_id, tenant_id = get_entra_env()
    # Full authority override, e.g. https://login.microsoftonline.com/<tenant> or .../organizations
    authority_raw = _authority_override()
    if authority_raw:
        authority: Any = authority_raw
    else:
        if not tenant_id:
            raise ValueError("AZURE_TENANT_ID is required unless AZURE_AUTHORITY is set.")
        login_host = _clean_id(os.getenv("AZURE_LOGIN_HOST")) or AZURE_PUBLIC
        authority = AuthorityBuilder(login_host, tenant_id)

    # Corporate proxies occasionally break instance discovery; disabling uses direct OIDC metadata URL.
    instance_discovery: Any = None
    if _env_bool("AZURE_MSAL_DISABLE_INSTANCE_DISCOVERY", False):
        instance_discovery = False

    # Use positional client_id — matches MSAL docs and avoids edge cases with keyword-only chains.
    return msal.PublicClientApplication(
        client_id,
        authority=authority,
        instance_discovery=instance_discovery,
        token_cache=token_cache,
    )


def try_acquire_token_silent(serialized_cache: str) -> tuple[str | None, str]:
    """Refresh access token from MSAL cache. Returns (access_token_or_None, serialized_cache_to_persist)."""
    if not (serialized_cache or "").strip():
        return None, serialized_cache
    cache = SerializableTokenCache()
    cache.deserialize(serialized_cache)
    app = build_public_client(token_cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        return None, serialized_cache
    result = app.acquire_token_silent(GRAPH_SCOPES, account=accounts[0])
    if not result or "access_token" not in result or result.get("error"):
        return None, serialized_cache
    token = (result.get("access_token") or "").strip()
    if not token:
        return None, serialized_cache
    if cache.has_state_changed:
        return token, cache.serialize()
    return token, serialized_cache


def describe_authority(app: msal.PublicClientApplication) -> Dict[str, str]:
    """Safe diagnostics to surface in UI (no secrets)."""
    auth = app.authority
    dae = getattr(auth, "device_authorization_endpoint", None) or ""
    te = getattr(auth, "token_endpoint", None) or ""
    return {
        "msal_tenant": getattr(auth, "tenant", "") or "",
        "token_endpoint": te[:120] + ("…" if len(te) > 120 else ""),
        "device_flow_endpoint": dae[:120] + ("…" if len(dae) > 120 else ""),
    }


def initiate_device_flow(app: msal.PublicClientApplication, scopes: List[str] | None = None) -> Dict[str, Any]:
    """Start device code flow. Caller must show flow['message'] to the user."""
    scopes = scopes or GRAPH_SCOPES
    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(
            flow.get("error_description") or flow.get("error") or "Failed to start device code flow."
        )
    return flow


def complete_device_flow(app: msal.PublicClientApplication, flow: Dict[str, Any]) -> Dict[str, Any]:
    """Block until user completes authentication in the browser. Returns token result dict."""
    return app.acquire_token_by_device_flow(flow)
