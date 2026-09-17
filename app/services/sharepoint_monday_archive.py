"""
Read-only Microsoft Graph access to the SharePoint folder holding the
Monday.com "Close" board attachments (the client manually uploaded them there
after downloading via data-extraction/download_attachments.py — the original
Monday S3 URLs in the board export are presigned and expire ~1 hour after
extraction, so SharePoint is the only usable source for the historical files).

Adapted from work/audit-management's SharePointAuditStorage (same Graph
client-credentials pattern) — this module only lists and downloads; it never
writes to SharePoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
from azure.identity.aio import ClientSecretCredential

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphError(RuntimeError):
    pass


@dataclass
class SharePointArchiveConfig:
    hostname: str = "avocarbongroup.sharepoint.com"
    site_path: str = "/sites/complaintsdata"
    library_name: str = "Documents"  # the "Shared Documents" library's drive name
    root_folder: str = "complaint-data/attachments/Close"


async def _get_access_token() -> str:
    tenant_id = os.getenv("AZURE_TENANT_ID")
    client_id = os.getenv("AZURE_CLIENT_ID")
    client_secret = os.getenv("AZURE_CLIENT_SECRET")
    if not (tenant_id and client_id and client_secret):
        raise GraphError(
            "AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET must be set"
        )
    credential = ClientSecretCredential(
        tenant_id=tenant_id, client_id=client_id, client_secret=client_secret
    )
    try:
        token = await credential.get_token("https://graph.microsoft.com/.default")
        return token.token
    finally:
        await credential.close()


class SharePointArchiveReader:
    def __init__(self, cfg: SharePointArchiveConfig | None = None):
        self.cfg = cfg or SharePointArchiveConfig()

    async def _headers(self) -> dict[str, str]:
        token = await _get_access_token()
        return {"Authorization": f"Bearer {token}"}

    async def _get_json(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(url, headers=await self._headers())
            if r.status_code >= 400:
                raise GraphError(f"GET {url} -> {r.status_code}: {r.text}")
            return r.json()

    # ---------- Lookups ----------
    async def get_site_id(self) -> str:
        url = f"{GRAPH_BASE}/sites/{self.cfg.hostname}:{self.cfg.site_path}"
        site = await self._get_json(url)
        return site["id"]

    async def get_drive_id(self, site_id: str) -> str:
        url = f"{GRAPH_BASE}/sites/{site_id}/drives"
        drives = await self._get_json(url)
        for d in drives.get("value", []):
            if d.get("name") == self.cfg.library_name:
                return d["id"]
        available = [d.get("name") for d in drives.get("value", [])]
        raise GraphError(
            f"Drive '{self.cfg.library_name}' not found. Available: {available}"
        )

    async def get_item_by_path(self, drive_id: str, sp_path: str) -> dict[str, Any]:
        sp_path = sp_path.lstrip("/")
        url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{sp_path}"
        return await self._get_json(url)

    async def list_children(self, drive_id: str, sp_path: str) -> list[dict[str, Any]]:
        """List immediate children (files + folders) of a folder, by path."""
        sp_path = sp_path.lstrip("/")
        url = (
            f"{GRAPH_BASE}/drives/{drive_id}/root:/{sp_path}:/children"
            if sp_path
            else f"{GRAPH_BASE}/drives/{drive_id}/root/children"
        )
        children: list[dict[str, Any]] = []
        while url:
            page = await self._get_json(url)
            children.extend(page.get("value", []))
            url = page.get("@odata.nextLink")
        return children

    async def walk_files(self, drive_id: str, sp_path: str) -> AsyncIterator[dict[str, Any]]:
        """Yield every file (not folder) under sp_path, recursively."""
        for child in await self.list_children(drive_id, sp_path):
            if "folder" in child:
                child_path = f"{sp_path.rstrip('/')}/{child['name']}"
                async for f in self.walk_files(drive_id, child_path):
                    yield f
            else:
                yield child

    async def download_bytes(self, drive_id: str, item_id: str) -> bytes:
        url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            r = await client.get(url, headers=await self._headers())
            if r.status_code >= 400:
                raise GraphError(f"GET content -> {r.status_code}: {r.text}")
            return r.content
