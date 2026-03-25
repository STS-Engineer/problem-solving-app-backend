"""
webhook_service.py  (v3)
────────────────────────
Push events (fired by complaints app):
  • complaint.created      — new complaint created (CS1 or CS2 only)
  • complaint.type_updated — quality_issue_warranty field changed on existing complaint
  • complaint.cancelled    — complaint status set to 'rejected' / manually cancelled

Pull endpoint for priorities lives in:
  app/api/endpoints/audit_priorities.py  (called by audit app planner)

Delivery infrastructure:
  run_one_poll()        — APScheduler every 2 min, delivers one pending job
  recover_locked_jobs() — APScheduler every 15 min, resets stale locked rows
  prune_old_jobs()      — APScheduler nightly 03:00 UTC, deletes old done/failed rows
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_webhook_settings
from app.core.email import _send_sync
from app.db.session import SessionLocal
from app.models.complaint import Complaint
from app.models.webhook_model import WebhookJob, WebhookStatus

log = logging.getLogger(__name__)

BACKOFF_SECONDS = [0, 60, 600]  # delay before attempt 1, 2, 3+
LOCKED_JOB_TTL_MINUTES = 10


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot dataclass — safe to use after DB session closes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _JobSnapshot:
    id: int
    complaint_ref: str
    target_url: str
    event: str
    payload_json: str
    attempt: int
    max_attempts: int


# ─────────────────────────────────────────────────────────────────────────────
# Payload builders
# ─────────────────────────────────────────────────────────────────────────────


def _complaint_dict(complaint: Complaint) -> dict[str, Any]:
    return {
        "id": complaint.id,
        "reference_number": complaint.reference_number,
        "complaint_name": complaint.complaint_name,
        "quality_issue_warranty": complaint.quality_issue_warranty,
        "customer": complaint.customer,
        "customer_plant_name": complaint.customer_plant_name,
        "avocarbon_plant": (
            complaint.avocarbon_plant.value if complaint.avocarbon_plant else None
        ),
        "product_line": (
            complaint.product_line.value if complaint.product_line else None
        ),
        "defects": complaint.defects,
        "repetition_count": _safe_int(complaint.repetitive_complete_with_number),
        "priority": complaint.priority,
        "status": complaint.status,
        "complaint_opening_date": (
            complaint.complaint_opening_date.isoformat()
            if complaint.complaint_opening_date
            else None
        ),
        "due_date": (complaint.due_date.isoformat() if complaint.due_date else None),
        "cqt_email": complaint.cqt_email,
        "quality_manager_email": complaint.quality_manager_email,
        "created_at": (
            complaint.created_at.isoformat() if complaint.created_at else None
        ),
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _build_payload(
    event: str,
    complaint: Complaint,
    job_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "webhook_event": event,
        "webhook_id": job_id,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "complaint": _complaint_dict(complaint),
    }
    if extra:
        payload.update(extra)
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Signing & HTTP delivery
# ─────────────────────────────────────────────────────────────────────────────


def _sign(secret: str, body: bytes) -> str:
    if not secret:
        return ""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post(snapshot: _JobSnapshot, cfg) -> tuple[bool, int | None, str | None]:
    body = snapshot.payload_json.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": snapshot.event,
        "X-Webhook-Signature": _sign(cfg.webhook_secret, body),
        "X-Delivery-Id": str(uuid.uuid4()),
        "X-Attempt-Number": str(snapshot.attempt),
        "User-Agent": "AVOCarbon-Webhook/2.0",
    }
    try:
        with httpx.Client(timeout=cfg.webhook_timeout_sec) as client:
            resp = client.post(snapshot.target_url, content=body, headers=headers)
        if resp.is_success:
            return True, resp.status_code, None
        return False, resp.status_code, f"HTTP {resp.status_code}"
    except httpx.TimeoutException as exc:
        return False, None, f"Timeout: {exc}"
    except Exception as exc:
        return False, None, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Core enqueue helper
# ─────────────────────────────────────────────────────────────────────────────


def _enqueue(
    db: Session,
    complaint: Complaint,
    event: str,
    payload_json: str,
) -> int:
    """
    Insert one WebhookJob row per configured target URL.
    Returns the number of jobs inserted.
    Must be called BEFORE db.commit() so it rolls back with the complaint.
    """
    cfg = get_webhook_settings()
    if not cfg.target_urls:
        log.warning(
            "WEBHOOK_TARGET not set — %s not enqueued for %s",
            event,
            complaint.reference_number,
        )
        return 0

    for url in cfg.target_urls:
        db.add(
            WebhookJob(
                complaint_id=complaint.id,
                complaint_ref=complaint.reference_number,
                complaint_type=(complaint.quality_issue_warranty or "").strip(),
                event=event,
                target_url=url,
                status=WebhookStatus.pending,
                attempt=0,
                max_attempts=cfg.webhook_max_attempts,
                retry_after=None,
                payload_json=payload_json,
            )
        )

    log.info(
        "Enqueued %d %s job(s) for %s",
        len(cfg.target_urls),
        event,
        complaint.reference_number,
    )
    return len(cfg.target_urls)


# ─────────────────────────────────────────────────────────────────────────────
# Public API — three push events
# ─────────────────────────────────────────────────────────────────────────────


def enqueue_complaint_created(db: Session, complaint: Complaint) -> None:
    """
    Call inside create_complaint() BEFORE db.commit().
    Only fires for CS1 and CS2 types.
    """
    cfg = get_webhook_settings()
    complaint_type = (complaint.quality_issue_warranty or "").strip()

    if complaint_type not in cfg.trigger_types:
        log.debug(
            "complaint.created skipped: %s type=%r not in trigger_types",
            complaint.reference_number,
            complaint_type,
        )
        return

    job_id = str(uuid.uuid4())
    payload_json = json.dumps(
        _build_payload("complaint.created", complaint, job_id),
        ensure_ascii=False,
        default=str,
    )
    _enqueue(db, complaint, "complaint.created", payload_json)


def enqueue_type_updated(
    db: Session,
    complaint: Complaint,
    old_type: str,
    new_type: str,
) -> None:
    """
    Call inside update_complaint() BEFORE db.commit(), when
    quality_issue_warranty has changed.

    Example:
        old = complaint.quality_issue_warranty
        complaint.quality_issue_warranty = new_value
        if old != new_value:
            enqueue_type_updated(db, complaint, old, new_value)
    """
    job_id = str(uuid.uuid4())
    payload_json = json.dumps(
        _build_payload(
            "complaint.type_updated",
            complaint,
            job_id,
            extra={"previous_type": old_type, "new_type": new_type},
        ),
        ensure_ascii=False,
        default=str,
    )
    _enqueue(db, complaint, "complaint.type_updated", payload_json)
    log.info(
        "complaint.type_updated enqueued: %s  %s → %s",
        complaint.reference_number,
        old_type,
        new_type,
    )


def enqueue_complaint_cancelled(db: Session, complaint: Complaint) -> None:
    """
    Call inside cancel_complaint() / when status is set to 'rejected'
    BEFORE db.commit().
    """
    job_id = str(uuid.uuid4())
    payload_json = json.dumps(
        _build_payload(
            "complaint.cancelled",
            complaint,
            job_id,
            extra={"cancelled_at": datetime.now(timezone.utc).isoformat()},
        ),
        ensure_ascii=False,
        default=str,
    )
    _enqueue(db, complaint, "complaint.cancelled", payload_json)


# ─────────────────────────────────────────────────────────────────────────────
# Delivery worker  (APScheduler every 2 min)
# ─────────────────────────────────────────────────────────────────────────────


def run_one_poll() -> None:
    snapshot = _claim_job()
    if snapshot is None:
        return
    cfg = get_webhook_settings()
    success, http_status, error = _post(snapshot, cfg)
    _update_job(snapshot, success, http_status, error)


def _claim_job() -> _JobSnapshot | None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.execute(
            text(
                """
                SELECT id FROM webhook_jobs
                WHERE status = 'pending'
                  AND (retry_after IS NULL OR retry_after <= :now)
                ORDER BY created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """
            ),
            {"now": now},
        ).fetchone()

        if row is None:
            return None

        job = db.get(WebhookJob, row.id)
        if job is None:
            return None

        job.status = WebhookStatus.locked
        job.attempt = (job.attempt or 0) + 1
        db.commit()

        return _JobSnapshot(
            id=job.id,
            complaint_ref=job.complaint_ref,
            target_url=job.target_url,
            event=job.event,
            payload_json=job.payload_json,
            attempt=job.attempt,
            max_attempts=job.max_attempts,
        )


def _update_job(
    snapshot: _JobSnapshot,
    success: bool,
    http_status: int | None,
    error: str | None,
) -> None:
    with SessionLocal() as db:
        job = db.get(WebhookJob, snapshot.id)
        if job is None:
            return

        job.last_http_status = http_status
        job.last_error = error

        try:
            if success:
                job.status = WebhookStatus.done
                log.info(
                    "Webhook delivered [job=%d complaint=%s event=%s attempt=%d http=%d]",
                    job.id,
                    job.complaint_ref,
                    job.event,
                    job.attempt,
                    http_status,
                )

            elif job.attempt >= job.max_attempts:
                job.status = WebhookStatus.failed
                log.error(
                    "Webhook abandoned [job=%d complaint=%s event=%s after %d attempts]: %s",
                    job.id,
                    job.complaint_ref,
                    job.event,
                    job.attempt,
                    error,
                )
                db.commit()
                _send_failure_email(snapshot, error)
                return

            else:
                delay = BACKOFF_SECONDS[min(job.attempt, len(BACKOFF_SECONDS) - 1)]
                job.status = WebhookStatus.pending
                job.retry_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
                log.warning(
                    "Webhook retry scheduled [job=%d attempt=%d/%d in %ds]: %s",
                    job.id,
                    job.attempt,
                    job.max_attempts,
                    delay,
                    error,
                )
        finally:
            db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Recovery & cleanup  (APScheduler)
# ─────────────────────────────────────────────────────────────────────────────


def recover_locked_jobs() -> int:
    """Reset jobs stuck in `locked` state after a process crash."""
    stale_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=LOCKED_JOB_TTL_MINUTES
    )
    with SessionLocal() as db:
        result = db.execute(
            text(
                """
                UPDATE webhook_jobs
                SET status = 'pending', retry_after = NULL
                WHERE status = 'locked' AND updated_at < :cutoff
            """
            ),
            {"cutoff": stale_cutoff},
        )
        db.commit()
    count = result.rowcount
    if count:
        log.warning("Recovered %d stale locked webhook job(s)", count)
    return count


def prune_old_jobs(keep_days: int = 7) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    with SessionLocal() as db:
        result = db.execute(
            text(
                """
                DELETE FROM webhook_jobs
                WHERE status IN ('done', 'failed')
                  AND updated_at < :cutoff
            """
            ),
            {"cutoff": cutoff},
        )
        db.commit()
    log.info(
        "Pruned %d old webhook job rows (older than %d days)",
        result.rowcount,
        keep_days,
    )
    return result.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# Failure alert email
# ─────────────────────────────────────────────────────────────────────────────


def _send_failure_email(snapshot: _JobSnapshot, error: str | None) -> None:
    cfg = get_webhook_settings()
    if not cfg.webhook_alert_emails:
        return

    subject = f"[AVOCarbon] Webhook permanently failed — {snapshot.complaint_ref}"
    body_html = f"""
    <p>Webhook delivery permanently failed after {snapshot.max_attempts} attempts.</p>
    <table cellpadding="6" style="border-collapse:collapse;font-family:sans-serif;font-size:14px">
      <tr><td><b>Complaint</b></td><td>{snapshot.complaint_ref}</td></tr>
      <tr><td><b>Event</b></td><td>{snapshot.event}</td></tr>
      <tr><td><b>Job ID</b></td><td>{snapshot.id}</td></tr>
      <tr><td><b>Target URL</b></td><td>{snapshot.target_url}</td></tr>
      <tr><td><b>Last error</b></td><td>{error or "unknown"}</td></tr>
    </table>
    <p style="color:#666;margin-top:12px">To requeue:<br>
    <code>UPDATE webhook_jobs SET status='pending', retry_after=NULL, attempt=0
    WHERE id={snapshot.id};</code></p>
    """

    def _fire() -> None:
        try:
            _send_sync(
                subject=subject,
                recipients=cfg.webhook_alert_emails,
                body_html=body_html,
                cc=None,
            )
            log.info("Failure alert sent for job=%d", snapshot.id)
        except Exception as exc:
            log.error("Failed to send alert email for job=%d: %s", snapshot.id, exc)

    threading.Thread(target=_fire, daemon=True).start()
