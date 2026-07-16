"""
app/services/blob_storage.py

Azure Blob Storage backend for escalation-action attachments
(signed POs, screenshots, email exports, …).

Adapted from the supplier-management project's blob service. Method names were
renamed for this domain (upload_opportunity_document → upload_escalation_attachment).

Configuration (see .env):
    AZURE_CONNECTION_STRING       – full account connection string
    AZURE_STORAGE_CONTAINER_NAME  – container name (created automatically)

The Azure SDK is imported lazily inside the functions so the app still boots
when the package is not installed or the feature is left unconfigured.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, urlunparse, unquote

from fastapi import UploadFile, HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── File validation ───────────────────────────────────────────────────────────
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB (escalation-action attachments)
INTAKE_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB (email-intake attachments)
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".xlsm",  # Excel macro-enabled
}

MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".xlsm": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


# ── Config helpers ──────────────────────────────────────────────────────────


def _get_azure_connection_string() -> str:
    return settings.AZURE_CONNECTION_STRING or os.getenv("AZURE_CONNECTION_STRING", "")


def _get_azure_container_name() -> str:
    return settings.AZURE_STORAGE_CONTAINER_NAME or os.getenv(
        "AZURE_STORAGE_CONTAINER_NAME", ""
    )


def is_configured() -> bool:
    """True when both the connection string and container name are set."""
    return bool(_get_azure_connection_string() and _get_azure_container_name())


def _require_configured() -> None:
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "File storage is not configured. Set AZURE_CONNECTION_STRING and "
                "AZURE_STORAGE_CONTAINER_NAME in the environment."
            ),
        )


def _parse_connection_string(conn_str: str) -> dict:
    """Parse an Azure connection string into a key/value dict."""
    return dict(part.split("=", 1) for part in conn_str.split(";") if "=" in part)


# ── URL helpers ───────────────────────────────────────────────────────────────


def _extract_blob_name(file_url: str) -> Optional[str]:
    """
    Extract the blob path from a full Azure URL.
    e.g. https://acct.blob.core.windows.net/attachments/escalations/foo.pdf
         → escalations/foo.pdf
    """
    try:
        parsed = urlparse(file_url)
        parts = parsed.path.lstrip("/").split("/", 1)  # [container, blob_name]
        return unquote(parts[1]) if len(parts) == 2 else None
    except Exception:
        return None


def _force_https_url(url: str) -> str:
    """Normalize Azure blob URLs to HTTPS to avoid mixed-content responses."""
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    if parsed.scheme != "http":
        return url
    hostname = (parsed.hostname or "").lower()
    if not hostname.endswith(".blob.core.windows.net"):
        return url
    return urlunparse(parsed._replace(scheme="https"))


def get_blob_sas_url(
    blob_name: str,
    expiry_days: int = 7,
    content_disposition: Optional[str] = None,
) -> str:
    """
    Generate a fresh read-only SAS URL for an existing blob.

    content_disposition: when set (e.g. 'attachment; filename="x.pdf"'), the
    browser is told to download the file instead of previewing it.
    """
    import datetime as dt

    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    connection_string = _get_azure_connection_string()
    container_name = _get_azure_container_name()
    parts = _parse_connection_string(connection_string)
    account_name = parts.get("AccountName", "")
    account_key = parts.get("AccountKey", "")

    container = _get_container_client()
    blob_client = container.get_blob_client(blob_name)

    if not account_name or not account_key:
        return _force_https_url(blob_client.url)  # fallback (no SAS)

    sas_token = generate_blob_sas(
        account_name=account_name,
        account_key=account_key,
        container_name=container_name,
        blob_name=blob_name,
        permission=BlobSasPermissions(read=True),
        expiry=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=expiry_days),
        content_disposition=content_disposition,
    )
    return f"{_force_https_url(blob_client.url)}?{sas_token}"


def get_blob_url(blob_name: str) -> str:
    """Long-lived URL suitable for storing alongside the audit event."""
    return get_blob_sas_url(blob_name, expiry_days=3650)  # 10 years


def get_blob_download_url(blob_name: str, filename: str) -> str:
    """Long-lived URL that forces a download (Content-Disposition: attachment)."""
    safe = _safe_filename(filename)
    return get_blob_sas_url(
        blob_name,
        expiry_days=3650,
        content_disposition=f'attachment; filename="{safe}"',
    )


def get_fresh_doc_url(file_url: str, expiry_days: int = 7) -> str:
    """Regenerate a fresh SAS URL from a previously stored blob URL."""
    blob_name = _extract_blob_name(file_url)
    if not blob_name:
        return _force_https_url(file_url)
    try:
        return get_blob_sas_url(blob_name, expiry_days=expiry_days)
    except Exception:
        return _force_https_url(file_url)


# ── Client factory (lazy singleton) ───────────────────────────────────────────

_blob_service_client = None  # type: ignore[var-annotated]


def _get_blob_service_client():
    global _blob_service_client
    if _blob_service_client is None:
        from azure.storage.blob import BlobServiceClient

        connection_string = _get_azure_connection_string()
        if not connection_string:
            raise RuntimeError("AZURE_CONNECTION_STRING is not configured.")
        _blob_service_client = BlobServiceClient.from_connection_string(
            connection_string
        )
    return _blob_service_client


def _get_container_client():
    container_name = _get_azure_container_name()
    if not container_name:
        raise RuntimeError("AZURE_STORAGE_CONTAINER_NAME is not configured.")
    client = _get_blob_service_client()
    container = client.get_container_client(container_name)
    # Ensure container exists (idempotent)
    try:
        container.get_container_properties()
    except Exception:
        container.create_container()
    return container


# ── Filename / path helpers ───────────────────────────────────────────────────


def _build_blob_name(folder: str, filename: str) -> str:
    """Blob path like: escalations/esc_12_D4_L2_20250101_120000_report.pdf"""
    return f"{folder}/{filename}"


def _safe_filename(original: str) -> str:
    """Strip path separators and problematic characters from a filename."""
    name = os.path.basename(original).strip()
    name = re.sub(r"[^\w.\-]", "_", name)
    return name or "file"


def _validate_extension(filename: str) -> str:
    """Return the lowercased extension or raise 400."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File type '{ext}' is not allowed. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            ),
        )
    return ext


# ── Public API ─────────────────────────────────────────────────────────────


async def upload_escalation_attachment(
    file: UploadFile,
    complaint_id: int,
    step_code: str,
    level: int,
) -> dict:
    """Upload a file attached to an escalation action and return its metadata."""
    safe_step = (
        "".join(ch for ch in step_code.lower() if ch.isalnum() or ch in ("_", "-"))
        or "step"
    )
    return await _upload_file(
        file=file,
        folder="escalations",
        prefix=f"esc_{complaint_id}_{safe_step}_l{level}",
    )


def upload_bytes(
    *,
    content: bytes,
    original_name: str,
    folder: str,
    prefix: str,
    mime_type: Optional[str] = None,
    max_size: int = INTAKE_MAX_FILE_SIZE,
) -> dict:
    """
    Upload raw bytes (already in memory, e.g. downloaded from a Graph URL) to
    Blob Storage. Synchronous — safe to call from the sync intake path.

    Returns {blob_name, file_url, filename, mimetype, size}.
    Raises HTTPException(400) on empty / oversized / disallowed-type files.
    """
    _require_configured()

    from azure.core.exceptions import AzureError
    from azure.storage.blob import ContentSettings

    original = _safe_filename(original_name or "file")
    ext = _validate_extension(original)  # raises 400 if not allowed

    size = len(content)
    if size == 0:
        raise HTTPException(status_code=400, detail="Downloaded file is empty.")
    if size > max_size:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File too large ({size / 1_048_576:.1f} MB). "
                f"Max allowed: {max_size // 1_048_576} MB."
            ),
        )

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    blob_filename = f"{prefix}_{timestamp}_{original}"
    blob_name = _build_blob_name(folder, blob_filename)

    mimetype = mime_type or MIME_TYPES.get(ext, "application/octet-stream")
    try:
        container = _get_container_client()
        blob_client = container.get_blob_client(blob_name)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=mimetype),
        )
        logger.info("Uploaded blob: %s  (%d bytes)", blob_name, size)
    except AzureError as exc:
        logger.error("Azure upload error for %s: %s", blob_name, exc)
        raise HTTPException(status_code=500, detail=f"File upload failed: {exc}")

    return {
        "blob_name": blob_name,
        "file_url": get_blob_url(blob_name),
        "filename": original,
        "mimetype": mimetype,
        "size": size,
    }


async def delete_blob(blob_name: str) -> bool:
    """
    Delete a blob by its full path inside the container.
    Returns True if deleted, False if it did not exist.
    """
    _require_configured()

    from azure.core.exceptions import AzureError

    try:
        container = _get_container_client()
        blob_client = container.get_blob_client(blob_name)
        blob_client.delete_blob()
        logger.info("Deleted blob: %s", blob_name)
        return True
    except AzureError as exc:
        if "BlobNotFound" in str(exc) or "404" in str(exc):
            logger.warning("Blob not found (already deleted?): %s", blob_name)
            return False
        logger.error("Error deleting blob %s: %s", blob_name, exc)
        raise HTTPException(status_code=500, detail=f"Error deleting file: {exc}")


# ── Internal ─────────────────────────────────────────────────────────────────


async def _upload_file(file: UploadFile, folder: str, prefix: str) -> dict:
    """Core upload logic shared by all upload helpers."""
    _require_configured()

    from azure.core.exceptions import AzureError
    from azure.storage.blob import ContentSettings

    # Validate extension
    original_name = _safe_filename(file.filename or "file")
    ext = _validate_extension(original_name)

    # Read content & validate size
    content = await file.read()
    size = len(content)
    if size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=(
                f"File too large ({size / 1_048_576:.1f} MB). "
                f"Max allowed: {MAX_FILE_SIZE // 1_048_576} MB."
            ),
        )

    # Build unique blob name
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    blob_filename = f"{prefix}_{timestamp}_{original_name}"
    blob_name = _build_blob_name(folder, blob_filename)

    # Upload to Azure
    mimetype = file.content_type or MIME_TYPES.get(ext, "application/octet-stream")
    try:
        container = _get_container_client()
        blob_client = container.get_blob_client(blob_name)
        blob_client.upload_blob(
            content,
            overwrite=True,
            content_settings=ContentSettings(content_type=mimetype),
        )
        logger.info("Uploaded blob: %s  (%d bytes)", blob_name, size)
    except AzureError as exc:
        logger.error("Azure upload error for %s: %s", blob_name, exc)
        raise HTTPException(status_code=500, detail=f"File upload failed: {exc}")

    file_url = get_blob_url(blob_name)

    return {
        "blob_name": blob_name,
        "file_url": file_url,
        "filename": original_name,
        "mimetype": mimetype,
        "size": size,
    }
