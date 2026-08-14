"""
app/services/graph_client.py

Thin wrapper around MSAL's confidential-client (client-credentials) flow to
acquire application-permission access tokens for Microsoft Graph, used by the
internal intake agent to read the shared claims mailbox and manage the
change-notification subscription that watches it.

Requires AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET (app.core.config)
and Mail.Read + Mail.ReadWrite application permissions, admin-consented, on
the app registration.
"""

from __future__ import annotations

import logging

import msal
import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
_GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

_msal_app: msal.ConfidentialClientApplication | None = None


class GraphNotConfigured(RuntimeError):
    """Raised when Graph credentials are missing — internal agent is disabled."""


def is_configured() -> bool:
    return bool(
        settings.AZURE_TENANT_ID
        and settings.AZURE_CLIENT_ID
        and settings.AZURE_CLIENT_SECRET
        and settings.GRAPH_MAILBOX_UPN
    )


def _get_msal_app() -> msal.ConfidentialClientApplication:
    global _msal_app
    if _msal_app is None:
        if not is_configured():
            raise GraphNotConfigured(
                "AZURE_TENANT_ID/AZURE_CLIENT_ID/AZURE_CLIENT_SECRET/GRAPH_MAILBOX_UPN "
                "must all be set to use the internal Graph intake agent."
            )
        authority = f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}"
        _msal_app = msal.ConfidentialClientApplication(
            client_id=settings.AZURE_CLIENT_ID,
            client_credential=settings.AZURE_CLIENT_SECRET,
            authority=authority,
        )
    return _msal_app


def get_access_token() -> str:
    """
    Returns a valid Graph access token, using MSAL's in-memory cache so we
    only hit the token endpoint again once the cached token is close to
    expiry.
    """
    app = _get_msal_app()
    result = app.acquire_token_silent(_GRAPH_SCOPE, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=_GRAPH_SCOPE)

    if "access_token" not in result:
        error = result.get("error")
        description = result.get("error_description")
        logger.error("Graph token acquisition failed: %s — %s", error, description)
        raise RuntimeError(f"Graph token acquisition failed: {error}: {description}")

    return result["access_token"]


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
    }


def _raise_with_body(resp: requests.Response) -> None:
    """
    requests.HTTPError's default str() only shows the URL and status code —
    Graph's actual error (e.g. "Subscription validation request failed...")
    is in the response body, which is exactly what you need to debug a
    rejected subscription/notification-url. Surface it in the message.
    """
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise requests.HTTPError(f"{exc} — body: {resp.text[:1000]}", response=resp) from exc


def graph_get(path: str, **kwargs) -> requests.Response:
    url = path if path.startswith("http") else f"{GRAPH_BASE_URL}{path}"
    resp = requests.get(url, headers=_auth_headers(), timeout=30, **kwargs)
    _raise_with_body(resp)
    return resp


def graph_post(path: str, json: dict, **kwargs) -> requests.Response:
    url = path if path.startswith("http") else f"{GRAPH_BASE_URL}{path}"
    resp = requests.post(url, headers=_auth_headers(), json=json, timeout=30, **kwargs)
    _raise_with_body(resp)
    return resp


def graph_patch(path: str, json: dict, **kwargs) -> requests.Response:
    url = path if path.startswith("http") else f"{GRAPH_BASE_URL}{path}"
    resp = requests.patch(url, headers=_auth_headers(), json=json, timeout=30, **kwargs)
    _raise_with_body(resp)
    return resp


def graph_delete(path: str, **kwargs) -> requests.Response:
    url = path if path.startswith("http") else f"{GRAPH_BASE_URL}{path}"
    resp = requests.delete(url, headers=_auth_headers(), timeout=30, **kwargs)
    _raise_with_body(resp)
    return resp
