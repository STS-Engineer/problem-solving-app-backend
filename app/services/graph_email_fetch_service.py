"""
app/services/graph_email_fetch_service.py

Task 2 of the internal intake agent migration: fetch the real content of a
message from Microsoft Graph once the webhook (Task 1) tells us a new
message id arrived. Pure fetch/read operations only — this module never
mutates the mailbox (no mark-as-read, no move). That happens only once the
full pipeline (classify → extract → ingest) actually succeeds, which is
Task 4 — see docs/internal-intake-agent-migration.md.

Mirrors the field shapes the external agent's payload already used (see
docs/intake-agent-prompt.md's "PIÈCES JOINTES" section) so Task 4 can hand
these straight to EmailIntakeCreate/AttachmentIn with minimal translation.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

from app.core.config import settings
from app.services.graph_client import graph_get, graph_patch, graph_post

logger = logging.getLogger(__name__)

_MESSAGE_SELECT = ",".join(
    [
        "id",
        "conversationId",
        "internetMessageId",
        "subject",
        "from",
        "sender",
        "toRecipients",
        "receivedDateTime",
        "bodyPreview",
        "body",
        "hasAttachments",
        "isRead",
    ]
)


def _mailbox_path(suffix: str) -> str:
    return f"/users/{settings.GRAPH_MAILBOX_UPN}{suffix}"


def fetch_message(message_id: str) -> dict[str, Any]:
    """
    Fetches one message's metadata + body from Graph, and shapes it into the
    same fields EmailIntakeCreate expects (source_message_id, subject,
    sender_email/name, received_at, raw_body, raw_html, conversation_id).

    Raises requests.HTTPError if the message can't be fetched (e.g. deleted
    before we got to it, or a permissions issue) — callers should treat that
    as a dropped notification, not retry indefinitely.
    """
    resp = graph_get(
        _mailbox_path(f"/messages/{message_id}"),
        params={"$select": _MESSAGE_SELECT},
    )
    msg = resp.json()

    sender = (msg.get("from") or {}).get("emailAddress") or {}
    body = msg.get("body") or {}
    is_html = (body.get("contentType") or "").lower() == "html"

    return {
        # Prefer the RFC 5322 Message-ID (matches what the external agent
        # sends today) so dedup keys line up if both paths ever run at once;
        # fall back to the Graph id if it's missing (rare, but seen on some
        # malformed/relayed mail).
        "source_message_id": msg.get("internetMessageId") or msg["id"],
        "graph_message_id": msg["id"],
        "conversation_id": msg.get("conversationId"),
        "sender_email": sender.get("address"),
        "sender_name": sender.get("name"),
        "subject": msg.get("subject"),
        "received_at": msg.get("receivedDateTime"),
        "raw_body": body.get("content") if not is_html else None,
        "raw_html": body.get("content") if is_html else None,
        "has_attachments": bool(msg.get("hasAttachments")),
        "is_read": bool(msg.get("isRead")),
    }


def fetch_message_attachments(message_id: str) -> list[dict[str, Any]]:
    """
    Fetches attachment metadata + content for a message. Returns a list
    shaped close to AttachmentIn (filename/mime_type/size/sha256/is_inline/
    content_id), plus a `content_bytes` key (raw bytes, not base64) that
    Task 4 will hand to blob storage directly — see the note in the
    migration doc about intake_attachments.py currently only accepting a
    `download_url` it fetches unauthenticated, which won't work for Graph's
    own attachment content (needs our bearer token). Task 4 needs to either
    upload these bytes directly, or add a bytes-accepting path to
    process_intake_attachments().

    Only `#microsoft.graph.fileAttachment` is handled (the common case —
    inline images, PDFs, Office docs, photos). itemAttachment (a forwarded
    email) and referenceAttachment (a OneDrive/SharePoint link) are recorded
    with status "unsupported_type" rather than silently dropped, so nothing
    disappears without a trace.

    Note: Graph inlines attachment content (`contentBytes`) directly in this
    response only up to ~3MB; larger attachments need the streaming
    `$value` endpoint, not implemented yet — recorded as "too_large".
    """
    resp = graph_get(_mailbox_path(f"/messages/{message_id}/attachments"))
    results: list[dict[str, Any]] = []

    for att in resp.json().get("value", []):
        odata_type = att.get("@odata.type", "")
        base_meta = {
            "filename": att.get("name") or "file",
            "mime_type": att.get("contentType"),
            "size": att.get("size"),
            "is_inline": bool(att.get("isInline")),
            "content_id": att.get("contentId"),
        }

        if odata_type != "#microsoft.graph.fileAttachment":
            results.append(
                {**base_meta, "status": "unsupported_type", "odata_type": odata_type}
            )
            continue

        content_b64 = att.get("contentBytes")
        if not content_b64:
            results.append({**base_meta, "status": "too_large"})
            continue

        content = base64.b64decode(content_b64)
        results.append(
            {
                **base_meta,
                "status": "fetched",
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_bytes": content,
            }
        )

    return results


def mark_message_read(message_id: str) -> None:
    graph_patch(_mailbox_path(f"/messages/{message_id}"), json={"isRead": True})


def move_message_to_folder(message_id: str, folder_name: str = "Processed") -> None:
    """
    Moves a message into `folder_name` (created at the mailbox root if it
    doesn't already exist). Mirrors what the external agent does manually
    today per docs/intake-agent-prompt.md's SOURCE section.
    """
    folder_id = _get_or_create_folder_id(folder_name)
    graph_post(
        _mailbox_path(f"/messages/{message_id}/move"), json={"destinationId": folder_id}
    )


def _get_or_create_folder_id(folder_name: str) -> str:
    resp = graph_get(
        _mailbox_path("/mailFolders"),
        params={"$filter": f"displayName eq '{folder_name}'"},
    )
    existing = resp.json().get("value", [])
    if existing:
        return existing[0]["id"]

    logger.info("Graph mailbox folder %r not found — creating it", folder_name)
    created = graph_post(
        _mailbox_path("/mailFolders"), json={"displayName": folder_name}
    )
    return created.json()["id"]
