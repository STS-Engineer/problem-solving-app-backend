"""
app/services/graph_subscription_service.py

Creates, renews and tears down the Microsoft Graph change-notification
subscription that watches the shared claims mailbox for new messages, so the
internal intake agent is pushed new mail instead of polling Outlook.

One row of app.models.graph_subscription.GraphSubscription tracks the active
subscription; a scheduled job (see app.services.scheduler) calls
renew_expiring_subscriptions() before Graph's ~2.9-day expiry lapses.
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email import _send_sync
from app.models.graph_subscription import GraphSubscription
from app.services.graph_client import graph_delete, graph_patch, graph_post

logger = logging.getLogger(__name__)

RESOURCE_TEMPLATE = "/users/{mailbox}/mailFolders('Inbox')/messages"
CHANGE_TYPE = "created"


def _resource() -> str:
    return RESOURCE_TEMPLATE.format(mailbox=settings.GRAPH_MAILBOX_UPN)


def _expiration(minutes: int | None = None) -> datetime:
    minutes = minutes or settings.GRAPH_SUBSCRIPTION_MAX_MINUTES
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def create_subscription(db: Session) -> GraphSubscription:
    """
    Registers a new Graph subscription for new-message notifications on the
    shared mailbox's Inbox, and stores it. Call once (e.g. via a one-off
    admin action) to bootstrap the internal agent; the renewal job keeps it
    alive afterwards.
    """
    if not settings.GRAPH_NOTIFICATION_URL:
        raise RuntimeError(
            "GRAPH_NOTIFICATION_URL must be set to a publicly reachable HTTPS "
            "URL before creating a Graph subscription."
        )

    client_state = settings.GRAPH_WEBHOOK_CLIENT_STATE or secrets.token_urlsafe(32)
    expiration = _expiration()

    payload = {
        "changeType": CHANGE_TYPE,
        "notificationUrl": settings.GRAPH_NOTIFICATION_URL,
        "resource": _resource(),
        "expirationDateTime": expiration.isoformat(),
        "clientState": client_state,
    }

    resp = graph_post("/subscriptions", json=payload)
    data = resp.json()

    row = GraphSubscription(
        subscription_id=data["id"],
        resource=data.get("resource", _resource()),
        change_type=data.get("changeType", CHANGE_TYPE),
        notification_url=data.get("notificationUrl", settings.GRAPH_NOTIFICATION_URL),
        client_state=client_state,
        expires_at=expiration,
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("Created Graph subscription %s, expires %s", row.subscription_id, row.expires_at)
    return row


def _send_subscription_alert(sub: GraphSubscription, reason: str) -> None:
    """
    Fire-and-forget email alert (mirrors webhook_service._send_failure_email)
    so a lapsed/broken mailbox subscription gets noticed immediately instead
    of silently — the subscription that expired 2026-08-17 went a month
    without anyone knowing until a manual DB check turned it up.
    """
    if not settings.GRAPH_ALERT_EMAILS:
        return

    subject = "[AVOCarbon] Email intake mailbox subscription problem"
    body_html = f"""
    <p>{reason}</p>
    <table cellpadding="6" style="border-collapse:collapse;font-family:sans-serif;font-size:14px">
      <tr><td><b>Mailbox</b></td><td>{settings.GRAPH_MAILBOX_UPN}</td></tr>
      <tr><td><b>Subscription ID</b></td><td>{sub.subscription_id}</td></tr>
      <tr><td><b>Expires at</b></td><td>{sub.expires_at.isoformat() if sub.expires_at else "unknown"}</td></tr>
      <tr><td><b>Last notification received</b></td><td>
        {sub.last_notification_at.isoformat() if sub.last_notification_at else "never"}</td></tr>
    </table>
    <p style="color:#666;margin-top:12px">
      New complaint emails sent to this mailbox are NOT being processed until this is fixed.<br>
      Re-subscribe: <code>POST /admin/graph/subscribe</code> on the backend, then confirm via
      <code>GET /admin/graph/subscription-status</code>.
    </p>
    """

    def _fire() -> None:
        try:
            _send_sync(
                subject=subject,
                recipients=settings.GRAPH_ALERT_EMAILS,
                body_html=body_html,
                cc=None,
            )
            logger.info("Graph subscription alert sent for %s", sub.subscription_id)
        except Exception as exc:
            logger.error(
                "Failed to send Graph subscription alert for %s: %s", sub.subscription_id, exc
            )

    threading.Thread(target=_fire, daemon=True).start()


def renew_subscription(db: Session, sub: GraphSubscription) -> None:
    expiration = _expiration()
    was_already_failing = bool(sub.last_error)
    try:
        graph_patch(
            f"/subscriptions/{sub.subscription_id}",
            json={"expirationDateTime": expiration.isoformat()},
        )
    except Exception as exc:
        sub.last_error = str(exc)[:1000]
        db.commit()
        logger.exception("Failed to renew Graph subscription %s", sub.subscription_id)
        # Only alert on the first failure, not every retry, to avoid spamming
        # if Graph/network stays down for a while.
        if not was_already_failing:
            _send_subscription_alert(sub, f"Renewal attempt failed: {exc}")
        raise

    sub.expires_at = expiration
    sub.last_renewed_at = datetime.now(timezone.utc)
    sub.status = "active"
    sub.last_error = None
    db.commit()
    logger.info("Renewed Graph subscription %s, new expiry %s", sub.subscription_id, expiration)


def _flag_lapsed_subscriptions(db: Session) -> None:
    """
    Catches a subscription that is still marked 'active' in our DB but has
    already passed its expires_at without ever being renewed — i.e.
    renew_expiring_subscriptions should have caught it and didn't (scheduler
    was down, app restarted, Graph/network was unreachable for a stretch,
    etc). Marks it 'expired' and alerts once on that transition, rather than
    leaving it silently active-but-dead like the subscription that lapsed
    2026-08-17 and wasn't noticed for a month.
    """
    now = datetime.now(timezone.utc)
    lapsed = (
        db.query(GraphSubscription)
        .filter(GraphSubscription.status == "active")
        .filter(GraphSubscription.expires_at < now)
        .all()
    )
    for sub in lapsed:
        sub.status = "expired"
        db.commit()
        logger.error(
            "Graph subscription %s expired without renewal (expired %s)",
            sub.subscription_id,
            sub.expires_at,
        )
        _send_subscription_alert(
            sub, f"Subscription expired at {sub.expires_at.isoformat()} without being renewed."
        )


def renew_expiring_subscriptions(db: Session) -> None:
    """Scheduler entry point — renews any active subscription nearing expiry."""
    margin = timedelta(minutes=settings.GRAPH_SUBSCRIPTION_RENEW_MARGIN_MINUTES)
    cutoff = datetime.now(timezone.utc) + margin

    due = (
        db.query(GraphSubscription)
        .filter(GraphSubscription.status == "active")
        .filter(GraphSubscription.expires_at <= cutoff)
        .all()
    )
    for sub in due:
        try:
            renew_subscription(db, sub)
        except Exception:
            # Already logged (and alerted, on first failure) in renew_subscription;
            # keep going for other rows.
            continue

    _flag_lapsed_subscriptions(db)


def delete_subscription(db: Session, sub: GraphSubscription) -> None:
    try:
        graph_delete(f"/subscriptions/{sub.subscription_id}")
    except Exception:
        logger.exception(
            "Failed to delete Graph subscription %s remotely — marking deleted locally anyway",
            sub.subscription_id,
        )
    sub.status = "deleted"
    db.commit()
