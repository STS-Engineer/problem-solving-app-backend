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

logger = logging.getLogger(__name__)


class WebhookService:
    def __init__(self):
        self.webhook_url = "https://your-app-a-url.com/api/webhooks/complaint-events"  # Configure via env
        self.webhook_secret = "your-webhook-secret"  # Configure via env
        self.max_retries = 3
        self.timeout = 5.0
    
    async def send_webhook_async(
        self, 
        event_type: str, 
        complaint_data: Dict[str, Any],
        complaint_id: int,
    ) -> bool:
        """
        Send webhook asynchronously with retry logic
        Returns True if successful, False otherwise
        """
        payload = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": complaint_data
        }
        
        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        self.webhook_url,
                        json=payload,
                        headers={
                            "X-Webhook-Secret": self.webhook_secret,
                            "Content-Type": "application/json"
                        },
                        timeout=self.timeout
                    )
                    
                    if response.status_code == 200:
                        logger.info(f"Webhook sent successfully: {event_type} for complaint {complaint_id}")
                        return True
                    else:
                        logger.warning(
                            f"Webhook failed with status {response.status_code}: "
                            f"{event_type} for complaint {complaint_id} (attempt {attempt + 1}/{self.max_retries})"
                        )
                        
            except Exception as e:
                logger.error(
                    f"Webhook error: {event_type} for complaint {complaint_id} "
                    f"(attempt {attempt + 1}/{self.max_retries}): {str(e)}"
                )
            
            # Exponential backoff: 1s, 2s, 4s
            if attempt < self.max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        
        return False
    
    def send_webhook_background(
        self,
        event_type: str,
        complaint_data: Dict[str, Any],
        complaint_id: int,
        db: Session
    ):
        """
        Trigger webhook send in background (fire and forget)
        Updates webhook tracking fields in database
        """
        async def _send_and_update():
            from app.models.complaint import Complaint
            
            success = await self.send_webhook_async(event_type, complaint_data, complaint_id)
            
            # Update webhook tracking
            complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
            if complaint:
                complaint.webhook_sent = success
                complaint.webhook_attempts += 1
                complaint.last_webhook_attempt = datetime.now(timezone.utc)
                db.commit()
                _send_failure_email(snapshot, error)
                return

            else:
                delay = BACKOFF_SECONDS[min(job.attempt, len(BACKOFF_SECONDS) - 1)]
                job.status      = WebhookStatus.pending
                job.retry_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
                log.warning("Webhook retry scheduled [job=%d attempt=%d/%d in %ds]: %s",
                            job.id, job.attempt, job.max_attempts, delay, error)
        finally:
            db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Recovery & cleanup  (APScheduler)
# ─────────────────────────────────────────────────────────────────────────────

def recover_locked_jobs() -> int:
    """Reset jobs stuck in `locked` state after a process crash."""
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOCKED_JOB_TTL_MINUTES)
    with SessionLocal() as db:
        result = db.execute(
            text("""
                UPDATE webhook_jobs
                SET status = 'pending', retry_after = NULL
                WHERE status = 'locked' AND updated_at < :cutoff
            """),
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
            text("""
                DELETE FROM webhook_jobs
                WHERE status IN ('done', 'failed')
                  AND updated_at < :cutoff
            """),
            {"cutoff": cutoff},
        )
        db.commit()
    log.info("Pruned %d old webhook job rows (older than %d days)",
             result.rowcount, keep_days)
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
            _send_sync(subject=subject, recipients=cfg.webhook_alert_emails,
                       body_html=body_html, cc=None)
            log.info("Failure alert sent for job=%d", snapshot.id)
        except Exception as exc:
            log.error("Failed to send alert email for job=%d: %s", snapshot.id, exc)

webhook_service = WebhookService()
    threading.Thread(target=_fire, daemon=True).start()
