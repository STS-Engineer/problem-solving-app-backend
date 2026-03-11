"""
app/services/file_storage.py

Centralised file-storage backend using the GitHub Contents API.
Files are stored as base64-encoded blobs inside the repository under:

    uploads/8d/<stored_name>          (e.g. uploads/8d/a3fb...1.jpg)

Environment variables required
-------------------------------
    GITHUB_TOKEN    Personal-access token with  repo  (or  contents:write) scope
    GITHUB_OWNER    GitHub user or organisation name  (e.g.  "my-org")
    GITHUB_REPO     Repository name                   (e.g.  "avocarbon-backend")
    GITHUB_BRANCH   Branch to commit files to         (default: "main")
    GITHUB_FOLDER   Folder inside the repo            (default: "uploads/8d")

Usage
-----
    from app.services.file_storage import storage

    # Upload → returns stored_name + raw URL
    result = await storage.upload(
        content=raw_bytes,
        original_name="photo.jpg",
        mime_type="image/jpeg",
    )
    # {"stored_name": "a3fb...jpg", "url": "https://raw.githubusercontent.com/..."}

    # Delete by stored_name
    await storage.delete("a3fb...jpg")

    # Build raw URL without a network call
    url = storage.url_for("a3fb...jpg")
"""

from __future__ import annotations

import base64
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"

_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
_OWNER  = os.environ.get("GITHUB_OWNER", "STS-Engineer")
_REPO   = os.environ.get("GITHUB_REPO", "problem-solving-app-backend")
_BRANCH = os.environ.get("GITHUB_BRANCH", "uploads")
_FOLDER = os.environ.get("GITHUB_FOLDER", "uploads/8d").rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _require_env() -> None:
    missing = [
        name for name, val in [
            ("GITHUB_TOKEN", _TOKEN),
            ("GITHUB_OWNER", _OWNER),
            ("GITHUB_REPO",  _REPO),
        ]
        if not val
    ]
    if missing:
        raise RuntimeError(
            f"GitHub file storage misconfigured. Missing env vars: {', '.join(missing)}"
        )


# ── Service ───────────────────────────────────────────────────────────────────


class FileStorageService:
    """
    Stores uploaded files directly inside a GitHub repository via the
    Contents API (PUT /repos/{owner}/{repo}/contents/{path}).

    stored_path in DB contains only the leaf filename:
        ``<uuid_hex><ext>``   →  e.g. ``a3f0b1c2d3e4f5a6.jpg``

    The full repo path is always:
        ``{GITHUB_FOLDER}/{stored_name}``
    """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def url_for(self, stored_name: str) -> str:
        """
        Return the raw GitHub URL for a stored file.
        NOTE: for private repos this URL requires a token — use fetch_content()
        to proxy the file through the backend instead.
        """
        return (
            f"https://raw.githubusercontent.com"
            f"/{_OWNER}/{_REPO}/{_BRANCH}/{_FOLDER}/{stored_name}"
        )

    def _repo_path(self, stored_name: str) -> str:
        """Full path inside the repo, e.g. 'uploads/8d/abc123.jpg'."""
        return f"{_FOLDER}/{stored_name}"

    async def fetch_content(self, stored_name: str) -> bytes:
        """
        Download the raw file bytes from GitHub using the token.
        Use this to proxy files from a private repo to the browser.

        Raises httpx.HTTPStatusError if the file is not found or token is invalid.
        """
        repo_path = self._repo_path(stored_name)
        api_url   = f"{_GITHUB_API}/repos/{_OWNER}/{_REPO}/contents/{repo_path}"

        async with httpx.AsyncClient(timeout=30) as client:
            # The Contents API returns base64-encoded content
            r = await client.get(
                api_url,
                headers={**_headers(), "Accept": "application/vnd.github.raw+json"},
                params={"ref": _BRANCH},
            )
            if r.status_code == 404:
                raise FileNotFoundError(f"File not found on GitHub: {stored_name}")
            r.raise_for_status()
            return r.content

    # ── Core operations ───────────────────────────────────────────────────────

    async def upload(
        self,
        content: bytes,
        original_name: str,
        mime_type: str,  # kept for interface parity; not used by GitHub API
    ) -> dict[str, str]:
        """
        Upload *content* to the GitHub repo and return::

            {
                "stored_name": "uuid.jpg",
                "url": "https://raw.githubusercontent.com/..."
            }

        Raises ``httpx.HTTPStatusError`` on GitHub API failure.
        """
        _require_env()

        ext = Path(original_name).suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{ext}"
        repo_path   = self._repo_path(stored_name)
        encoded     = base64.b64encode(content).decode()

        api_url = f"{_GITHUB_API}/repos/{_OWNER}/{_REPO}/contents/{repo_path}"

        payload: dict[str, Any] = {
            "message": f"chore: upload evidence file {stored_name}",
            "content": encoded,
            "branch":  _BRANCH,
        }

        async with httpx.AsyncClient(headers=_headers(), timeout=60) as client:
            r = await client.put(api_url, json=payload)
            r.raise_for_status()

        download_url = self.url_for(stored_name)
        logger.info("Uploaded %s → %s", stored_name, download_url)

        return {"stored_name": stored_name, "url": download_url}

    async def delete(self, stored_name: str) -> None:
        """
        Delete a file from the repository.

        GitHub requires the blob SHA to delete — we fetch it first, then DELETE.
        Silently ignores 404 (file already gone or never uploaded).
        """
        _require_env()

        repo_path = self._repo_path(stored_name)
        api_url   = f"{_GITHUB_API}/repos/{_OWNER}/{_REPO}/contents/{repo_path}"

        async with httpx.AsyncClient(headers=_headers(), timeout=30) as client:
            # 1. Fetch current blob SHA
            r_get = await client.get(api_url, params={"ref": _BRANCH})

            if r_get.status_code == 404:
                logger.warning("delete: file not found on GitHub, skipping: %s", stored_name)
                return

            r_get.raise_for_status()
            sha = r_get.json()["sha"]

            # 2. Delete the blob
            r_del = await client.request(
                "DELETE",
                api_url,
                json={
                    "message": f"chore: delete evidence file {stored_name}",
                    "sha":     sha,
                    "branch":  _BRANCH,
                },
            )
            r_del.raise_for_status()

        logger.info("Deleted %s from GitHub repo", stored_name)


# ── Module-level singleton ────────────────────────────────────────────────────
# Use this everywhere — no need to instantiate per request.
#
#   from app.services.file_storage import storage
#
storage = FileStorageService()