"""
app/services/file_storage.py

Centralised file-storage backend using the GitHub Contents API.
Files are stored as base64-encoded blobs inside the repository under:
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

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"

_DEFAULT_TOKEN  = ""
_DEFAULT_OWNER  = "STS-Engineer"
_DEFAULT_REPO   = "problem-solving-app-backend"
_DEFAULT_BRANCH = "uploads"
_DEFAULT_FOLDER = "uploads/8d"


def _token()  -> str: return os.environ.get("GITHUB_TOKEN",  _DEFAULT_TOKEN).strip()
def _owner()  -> str: return os.environ.get("GITHUB_OWNER",  _DEFAULT_OWNER).strip()
def _repo()   -> str: return os.environ.get("GITHUB_REPO",   _DEFAULT_REPO).strip()
def _branch() -> str: return os.environ.get("GITHUB_BRANCH", _DEFAULT_BRANCH).strip()
def _folder() -> str: return os.environ.get("GITHUB_FOLDER", _DEFAULT_FOLDER).strip().rstrip("/")


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
        name for name, val in [
            ("GITHUB_TOKEN", _token()),
            ("GITHUB_OWNER", _owner()),
            ("GITHUB_REPO",  _repo()),
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
    Stores uploaded files directly inside a GitHub repository via the
    Contents API (PUT /repos/{owner}/{repo}/contents/{path}).

    stored_path in DB = leaf filename only:
        <uuid_hex><ext>   e.g. a3f0b1c2d3e4f5a6.jpg

    Full repo path:
        {GITHUB_FOLDER}/{stored_name}
    """

    def _repo_path(self, stored_name: str) -> str:
        return f"{_folder()}/{stored_name}"

    def url_for(self, stored_name: str) -> str:
        """
        Raw GitHub URL — only works for public repos.
        For private repos use fetch_content() to proxy through the backend.
        """
        return (
            f"https://raw.githubusercontent.com"
            f"/{_owner()}/{_repo()}/{_branch()}/{_folder()}/{stored_name}"
        )

    async def fetch_content(self, stored_name: str) -> bytes:
        """
        Download raw file bytes via the GitHub API (works for private repos).
        Use this to proxy files to the browser instead of redirecting.
        """
        api_url = (
            f"{_GITHUB_API}/repos/{_owner()}/{_repo()}"
            f"/contents/{self._repo_path(stored_name)}"
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

    async def upload(
        self,
        content: bytes,
        original_name: str,
        mime_type: str,
    ) -> dict[str, str]:
        """
        Upload bytes to GitHub and return:
            {"stored_name": "uuid.jpg", "url": "https://raw.githubusercontent.com/..."}
        """
        _require_env()

        ext         = Path(original_name).suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{ext}"
        repo_path   = self._repo_path(stored_name)
        encoded     = base64.b64encode(content).decode()

        api_url = f"{_GITHUB_API}/repos/{_owner()}/{_repo()}/contents/{repo_path}"

        payload: dict[str, Any] = {
            "message": f"chore: upload evidence file {stored_name}",
            "content": encoded,
            "branch":  _branch(),
        }

        async with httpx.AsyncClient(headers=_headers(), timeout=60) as client:
            r = await client.put(api_url, json=payload)
            r.raise_for_status()

        download_url = self.url_for(stored_name)
        logger.info("Uploaded %s -> %s", stored_name, download_url)
        return {"stored_name": stored_name, "url": download_url}

    async def delete(self, stored_name: str) -> None:
        """
        Delete a file from GitHub.
        Fetches the blob SHA first (required by the API), then deletes.
        Silently ignores 404.
        """
        _require_env()

        repo_path = self._repo_path(stored_name)
        api_url   = f"{_GITHUB_API}/repos/{_owner()}/{_repo()}/contents/{repo_path}"

        async with httpx.AsyncClient(headers=_headers(), timeout=30) as client:
            r_get = await client.get(api_url, params={"ref": _branch()})

            if r_get.status_code == 404:
                logger.warning("delete: file not found on GitHub, skipping: %s", stored_name)
                return

            r_get.raise_for_status()
            sha = r_get.json()["sha"]

            r_del = await client.request(
                "DELETE",
                api_url,
                json={
                    "message": f"chore: delete evidence file {stored_name}",
                    "sha":     sha,
                    "branch":  _branch(),
                },
            )
            r_del.raise_for_status()

        logger.info("Deleted %s from GitHub repo", stored_name)


# ── Singleton — import this everywhere ───────────────────────────────────────
storage = FileStorageService()