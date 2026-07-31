"""
app/services/file_storage.py

Centralised file-storage backend used by 8D evidence uploads (D2e/D6/D7 via
step_files.py + conversation.py) and PDF report exports.

New uploads go to Azure Blob Storage (see app/services/blob_storage.py,
configured via AZURE_CONNECTION_STRING + AZURE_STORAGE_CONTAINER_NAME).

Files uploaded before this migration live in the legacy GitHub-Contents-API
backend and are still served/deleted from there. The two are told apart by
`stored_path` shape: legacy GitHub entries are a bare leaf filename
("<uuid>.ext"), Azure blob entries always include a folder prefix
("evidence/8d/<uuid>.ext") — so no schema change/migration was needed.
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

load_dotenv()
import httpx

from app.services import blob_storage

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"

_DEFAULT_TOKEN = ""
_DEFAULT_OWNER = "STS-Engineer"
_DEFAULT_REPO = "problem-solving-app-backend"
_DEFAULT_BRANCH = "uploads"
_DEFAULT_FOLDER = "uploads/8d"
_DEFAULT_REPORTS_FOLDER = "exports/8d-reports"

_BLOB_EVIDENCE_FOLDER = "evidence/8d"
_BLOB_REPORTS_FOLDER = "reports/8d"


def _is_blob_path(stored_name: str) -> bool:
    """Azure blob names always carry a folder prefix; legacy GitHub leaf
    filenames never do."""
    return "/" in stored_name


def _token() -> str:
    return os.environ.get("GITHUB_TOKEN", _DEFAULT_TOKEN).strip()


def _owner() -> str:
    return os.environ.get("GITHUB_OWNER", _DEFAULT_OWNER).strip()


def _repo() -> str:
    return os.environ.get("GITHUB_REPO", _DEFAULT_REPO).strip()


def _branch() -> str:
    return os.environ.get("GITHUB_BRANCH", _DEFAULT_BRANCH).strip()


def _folder() -> str:
    return os.environ.get("GITHUB_FOLDER", _DEFAULT_FOLDER).strip().rstrip("/")


def _reports_folder() -> str:
    return (
        os.environ.get("GITHUB_REPORTS_FOLDER", _DEFAULT_REPORTS_FOLDER)
        .strip()
        .rstrip("/")
    )


def _headers() -> dict[str, str]:
    token = _token()
    if not token:
        raise RuntimeError("GITHUB_TOKEN is empty — check your config")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _require_env() -> None:
    missing = [
        name
        for name, val in [
            ("GITHUB_TOKEN", _token()),
            ("GITHUB_OWNER", _owner()),
            ("GITHUB_REPO", _repo()),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"GitHub file storage misconfigured. Missing: {', '.join(missing)}"
        )


# ── Service ───────────────────────────────────────────────────────────────────


class FileStorageService:
    """
    New files: uploaded to Azure Blob Storage via the Azure SDK
    (app/services/blob_storage.py's container client).

    Legacy files (uploaded before this migration): stored inside a GitHub
    repository via the Contents API (PUT /repos/{owner}/{repo}/contents/{path}).

    stored_path in DB:
        Azure  = "<folder>/<uuid_hex><ext>"   e.g. evidence/8d/a3f0b1c2.jpg
        GitHub = "<uuid_hex><ext>"            e.g. a3f0b1c2.jpg   (legacy)
    """

    # ── Azure blob backend (current) ───────────────────────────────────────

    def _blob_path(self, stored_name: str, folder: str) -> str:
        return f"{folder}/{stored_name}"

    async def _blob_upload(self, content: bytes, original_name: str, mime_type: str, folder: str) -> dict[str, str]:
        from azure.storage.blob import ContentSettings

        ext = Path(original_name).suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{ext}"
        blob_name = self._blob_path(stored_name, folder)

        container = blob_storage.get_container_client()
        blob_client = container.get_blob_client(blob_name)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=mime_type),
        )
        url = blob_storage.get_blob_url(blob_name)
        logger.info("Uploaded %s -> %s", blob_name, url)
        return {"stored_name": blob_name, "url": url}

    async def _blob_upload_named(self, content: bytes, blob_name: str, mime_type: str) -> dict[str, str]:
        """Upload with an exact, caller-chosen blob name (no uuid substitution)
        — used for report exports, which are addressed by filename, not id."""
        from azure.storage.blob import ContentSettings

        container = blob_storage.get_container_client()
        blob_client = container.get_blob_client(blob_name)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=mime_type),
        )
        url = blob_storage.get_blob_url(blob_name)
        logger.info("Uploaded %s -> %s", blob_name, url)
        return {"stored_name": blob_name, "url": url}

    def _blob_url_for(self, blob_name: str) -> str:
        return blob_storage.get_blob_url(blob_name)

    async def _blob_fetch_content(self, blob_name: str) -> bytes:
        container = blob_storage.get_container_client()
        blob_client = container.get_blob_client(blob_name)
        try:
            return blob_client.download_blob().readall()
        except Exception as exc:
            if "BlobNotFound" in str(exc) or "404" in str(exc):
                raise FileNotFoundError(f"File not found in blob storage: {blob_name}")
            raise

    async def _blob_delete(self, blob_name: str) -> None:
        container = blob_storage.get_container_client()
        blob_client = container.get_blob_client(blob_name)
        try:
            blob_client.delete_blob()
            logger.info("Deleted %s from blob storage", blob_name)
        except Exception as exc:
            if "BlobNotFound" in str(exc) or "404" in str(exc):
                logger.warning("delete: blob not found, skipping: %s", blob_name)
                return
            raise

    # ── Legacy GitHub backend (old files only) ──────────────────────────────

    def _github_repo_path(self, stored_name: str, folder: str) -> str:
        return f"{folder}/{stored_name}"

    def _github_url_for(self, stored_name: str) -> str:
        return (
            f"https://raw.githubusercontent.com"
            f"/{_owner()}/{_repo()}/{_branch()}/{_folder()}/{stored_name}"
        )

    async def _github_fetch_content(self, stored_name: str) -> bytes:
        api_url = (
            f"{_GITHUB_API}/repos/{_owner()}/{_repo()}"
            f"/contents/{self._github_repo_path(stored_name, _folder())}"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                api_url,
                headers={**_headers(), "Accept": "application/vnd.github.raw+json"},
                params={"ref": _branch()},
            )
            if r.status_code == 404:
                raise FileNotFoundError(f"File not found on GitHub: {stored_name}")
            r.raise_for_status()
            return r.content

    async def _github_delete(self, stored_name: str) -> None:
        repo_path = self._github_repo_path(stored_name, _folder())
        api_url = f"{_GITHUB_API}/repos/{_owner()}/{_repo()}/contents/{repo_path}"

        async with httpx.AsyncClient(headers=_headers(), timeout=30) as client:
            r_get = await client.get(api_url, params={"ref": _branch()})

            if r_get.status_code == 404:
                logger.warning(
                    "delete: file not found on GitHub, skipping: %s", stored_name
                )
                return

            r_get.raise_for_status()
            sha = r_get.json()["sha"]

            r_del = await client.request(
                "DELETE",
                api_url,
                json={
                    "message": f"chore: delete evidence file {stored_name}",
                    "sha": sha,
                    "branch": _branch(),
                },
            )
            r_del.raise_for_status()

        logger.info("Deleted %s from GitHub repo", stored_name)

    # ── Public API (backend-agnostic — dispatches by stored_path shape) ─────

    def url_for(self, stored_name: str) -> str:
        if _is_blob_path(stored_name):
            return self._blob_url_for(stored_name)
        return self._github_url_for(stored_name)

    async def fetch_content(self, stored_name: str) -> bytes:
        if _is_blob_path(stored_name):
            return await self._blob_fetch_content(stored_name)
        return await self._github_fetch_content(stored_name)

    async def upload(
        self,
        content: bytes,
        original_name: str,
        mime_type: str,
    ) -> dict[str, str]:
        """
        Upload bytes to Azure Blob Storage and return:
            {"stored_name": "evidence/8d/uuid.jpg", "url": "https://...blob.core.windows.net/..."}
        """
        return await self._blob_upload(content, original_name, mime_type, _BLOB_EVIDENCE_FOLDER)

    async def delete(self, stored_name: str) -> None:
        if _is_blob_path(stored_name):
            await self._blob_delete(stored_name)
        else:
            await self._github_delete(stored_name)

    async def upload_report(
        self,
        content: bytes,
        filename: str,
    ) -> dict[str, str]:
        blob_name = self._blob_path(filename, _BLOB_REPORTS_FOLDER)
        return await self._blob_upload_named(content, blob_name, "application/octet-stream")

    def url_for_report(self, filename: str) -> str:
        blob_name = self._blob_path(filename, _BLOB_REPORTS_FOLDER)
        return self._blob_url_for(blob_name)


# ── Singleton — import this everywhere ───────────────────────────────────────
storage = FileStorageService()
