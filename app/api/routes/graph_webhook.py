"""
app/api/routes/graph_webhook.py

Receives Microsoft Graph change notifications for the shared claims mailbox
(the push side of the internal intake agent, replacing the external MCP
agent's polling). Two responsibilities only:

1. Answer Graph's subscription-validation handshake (a `validationToken`
   query param that must be echoed back as text/plain within 10s).
2. Accept new-message notifications, check `clientState`, and hand off the
   actual fetch/classify/extract/ingest work to a background task so this
   endpoint can ack within Graph's timeout.

The actual email-fetch + LLM classification/extraction (tasks 2 & 3 of the
internal-agent migration) are not implemented here yet — `_handle_notification`
is a placeholder hook for that work.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.graph_subscription import GraphSubscription
from app.services.graph_email_fetch_service import fetch_message, fetch_message_attachments

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/mailbox", summary="Graph change-notification receiver (validation + new mail)")
async def graph_mailbox_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    validationToken: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    # ── 1. Subscription validation handshake ────────────────────────────
    # Graph calls this URL with ?validationToken=... when a subscription is
    # created/renewed and expects the exact token echoed back as plain text,
    # 200 OK, within 10 seconds.
    if validationToken is not None:
        return Response(content=validationToken, media_type="text/plain", status_code=200)

    # ── 2. Actual change notifications ───────────────────────────────────
    body = await request.json()
    notifications = body.get("value", [])

    accepted = 0
    for note in notifications:
        client_state = note.get("clientState")
        if not _is_known_client_state(db, client_state):
            logger.warning("Graph webhook: unrecognized clientState — dropping notification")
            continue

        message_id = (note.get("resourceData") or {}).get("id")
        subscription_id = note.get("subscriptionId")
        if not message_id:
            logger.warning("Graph webhook: notification missing resourceData.id — skipping")
            continue

        background_tasks.add_task(_handle_notification, subscription_id, message_id)
        accepted += 1

    logger.info("Graph webhook: accepted %d/%d notification(s)", accepted, len(notifications))
    # Graph only cares about a 202 within its timeout; per-item errors are
    # logged, not surfaced, so a bad notification never causes Graph to
    # retry/back off the whole batch.
    return Response(status_code=202)


def _is_known_client_state(db: Session, client_state: str | None) -> bool:
    if not client_state:
        return False
    if settings.GRAPH_WEBHOOK_CLIENT_STATE:
        return client_state == settings.GRAPH_WEBHOOK_CLIENT_STATE
    return (
        db.query(GraphSubscription)
        .filter(GraphSubscription.client_state == client_state)
        .filter(GraphSubscription.status == "active")
        .first()
        is not None
    )


def _handle_notification(subscription_id: str | None, message_id: str) -> None:
    """
    Fetches the message + attachments via Graph (task 2 — internal
    email-fetch service) and logs what was found. Classification/extraction
    (task 3) and actually calling EmailIntakeService.ingest (task 4) are not
    wired in yet — deliberately: the mailbox is never mutated (no
    mark-as-read, no move-to-Processed) from this fetch-only step, so
    nothing is filed away as handled before it's actually turned into an
    intake. That happens only once Task 4 lands.

    Also stamps a breadcrumb (last_notification_at/message_id, a running
    count) onto the GraphSubscription row so "did a notification arrive?"
    can be checked via the DB / admin status endpoint instead of only logs.
    Runs in a background task (after the HTTP response is sent), so it opens
    its own DB session rather than reusing the request-scoped one.
    """
    try:
        message = fetch_message(message_id)
        attachments = fetch_message_attachments(message_id) if message["has_attachments"] else []
        logger.info(
            "Graph webhook: fetched message %s (subscription %s) — subject=%r "
            "from=%r conversation_id=%s attachments=%d — classify/extract/ingest not wired yet",
            message_id,
            subscription_id,
            message.get("subject"),
            message.get("sender_email"),
            message.get("conversation_id"),
            len(attachments),
        )
    except Exception:
        logger.exception(
            "Graph webhook: failed to fetch message %s (subscription %s)",
            message_id,
            subscription_id,
        )

    db = SessionLocal()
    try:
        query = db.query(GraphSubscription)
        sub = (
            query.filter(GraphSubscription.subscription_id == subscription_id).first()
            if subscription_id
            else None
        )
        # Fall back to "the active subscription" if Graph didn't echo an id
        # we recognize (shouldn't normally happen, but keeps the breadcrumb
        # useful rather than silently no-op'ing).
        if sub is None:
            sub = query.filter(GraphSubscription.status == "active").first()
        if sub is not None:
            sub.last_notification_at = datetime.now(timezone.utc)
            sub.last_notification_message_id = message_id
            sub.notification_count = (sub.notification_count or 0) + 1
            db.commit()
    except Exception:
        logger.exception("Failed to record notification breadcrumb for message %s", message_id)
        db.rollback()
    finally:
        db.close()
