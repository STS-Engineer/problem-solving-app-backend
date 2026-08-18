"""
app/services/internal_intake_pipeline.py

Task 4 of the internal intake agent migration: wires Tasks 1-3 together.
Given a Graph message id (from the webhook notification), this is the
single entry point that:

  1. fetches the message + attachments (Task 2, graph_email_fetch_service)
  2. classifies + extracts (Task 3, graph_email_classify_service)
  3. calls EmailIntakeService.ingest() directly — no external MCP hop
  4. stores real attachment content (Task 2's fetched bytes, via
     intake_attachments.store_fetched_attachments — NOT the download_url
     path, which doesn't apply here; see the Task 2 note in the migration
     doc about why these are different)
  5. only THEN — once ingest() has succeeded — marks the message read and
     moves it to "Processed", per the Task 2 decision to never mutate the
     mailbox before the pipeline actually completes

A message is left alone (not marked read, not moved) if any step raises —
so a bug leaves it visibly unprocessed in Inbox for retry/manual triage,
rather than silently filed away.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.enums import PlantEnum
from app.schemas.email_intake import EmailIntakeCreate
from app.services.email_intake_service import EmailIntakeService
from app.services.graph_email_classify_service import classify_and_extract
from app.services.graph_email_fetch_service import (
    fetch_message,
    fetch_message_attachments,
    mark_message_read,
    move_message_to_folder,
)
from app.services.intake_attachments import (
    link_intake_files_to_complaint,
    store_fetched_attachments,
)

logger = logging.getLogger(__name__)


def _safe_plant(value: Optional[str]) -> Optional[PlantEnum]:
    if not value:
        return None
    try:
        return PlantEnum(str(value).strip().upper())
    except ValueError:
        return None


def _apply_fetched_attachments(
    db: Session, intake, ingest_status: str, source_message_id: str, enriched: list[dict]
) -> None:
    """
    Merges the real, stored attachment metadata (from store_fetched_attachments)
    into the intake row. Shape mirrors EmailIntakeService.ingest()'s own
    handling — see app/services/email_intake_service.py for the equivalent
    logic on the create/attach-to-existing paths.
    """
    if ingest_status == "created":
        intake.attachments = enriched
    elif ingest_status == "attached_to_existing":
        followups = list(intake.attachments or [])
        for entry in reversed(followups):
            if entry.get("type") == "followup_email" and entry.get("source_message_id") == source_message_id:
                entry["attachments"] = enriched
                break
        intake.attachments = followups
    else:
        return  # duplicate — nothing new to attach

    db.commit()
    db.refresh(intake)

    if intake.complaint_id:
        link_intake_files_to_complaint(db, intake.id, intake.complaint_id)


def handle_new_message(db: Session, message_id: str, *, mutate_mailbox: bool = True) -> dict:
    """
    Full pipeline for one Graph message id. Returns a summary dict for
    logging/testing:
      {"outcome": "skipped" | "created" | "duplicate" | "attached_to_existing" | "error", ...}

    mutate_mailbox=False skips mark-as-read/move-to-Processed — used by
    tests so a dry run against the real mailbox doesn't file real email
    away. Production code should always leave this True.
    """
    message = fetch_message(message_id)
    attachments = fetch_message_attachments(message_id) if message["has_attachments"] else []

    result = classify_and_extract(
        subject=message.get("subject"),
        sender_email=message.get("sender_email"),
        sender_name=message.get("sender_name"),
        raw_body=message.get("raw_body"),
        raw_html=message.get("raw_html"),
        attachments=attachments,
    )

    if not result["is_complaint"]:
        logger.info(
            "internal_intake: message %s classified as NOT a complaint (%s) — skipping",
            message_id,
            result.get("skip_reason"),
        )
        if mutate_mailbox:
            mark_message_read(message_id)
            move_message_to_folder(message_id)
        return {"outcome": "skipped", "reason": result.get("skip_reason")}

    payload = EmailIntakeCreate(
        source_message_id=message["source_message_id"],
        conversation_id=message.get("conversation_id"),
        sender_email=message.get("sender_email"),
        sender_name=message.get("sender_name"),
        subject=message.get("subject"),
        received_at=_parse_received_at(message.get("received_at")),
        raw_body=message.get("raw_body"),
        raw_html=message.get("raw_html"),
        attachments=[],  # real content stored separately, see below
        extracted_data=result["extracted_data"],
        ai_notes=result.get("ai_notes"),
        missing_fields=result.get("missing_fields") or [],
        detected_plant=_safe_plant(result.get("detected_plant")),
    )

    intake, status = EmailIntakeService.ingest(db, payload)

    if attachments and status != "duplicate":
        enriched = store_fetched_attachments(db, intake.id, attachments)
        _apply_fetched_attachments(db, intake, status, message["source_message_id"], enriched)

    if mutate_mailbox:
        mark_message_read(message_id)
        move_message_to_folder(message_id)

    logger.info(
        "internal_intake: message %s -> intake %s (%s)", message_id, intake.id, status
    )
    return {"outcome": status, "intake_id": intake.id}


def _parse_received_at(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
