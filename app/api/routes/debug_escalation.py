# app/api/routes/debug_escalation.py
from __future__ import annotations

import os
import smtplib
import socket
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db          # adjust to your actual dependency
from app.models.complaint import Complaint
from app.models.email_outbox import EmailOutbox
from app.models.report import Report
from app.models.report_step import ReportStep
from app.services.escalation_service import (
    _hours_overdue,
    _level_to_send,
    _build_recipients,
    _build_cc,
    _get_thresholds,
)
from app.services.scheduler import is_scheduler_running
from app.core.email import _send_sync

router = APIRouter(prefix="/debug", tags=["debug"])


# ── 1. Full system status ─────────────────────────────────────────────────────

@router.get("/status")
def debug_status(db: Session = Depends(get_db)):
    """
    Returns scheduler state, SMTP config, overdue step count,
    and outbox summary — no logs needed.
    """
    # Scheduler
    scheduler_ok = is_scheduler_running()

    # SMTP env
    smtp_info = {
        "SMTP_HOST": os.getenv("SMTP_HOST", "NOT SET"),
        "SMTP_PORT": os.getenv("SMTP_PORT", "NOT SET"),
        "SMTP_USER": os.getenv("SMTP_USER", "NOT SET"),
        "SMTP_PASSWORD_SET": bool(os.getenv("SMTP_PASSWORD")),
    }

    # DB: overdue steps
    overdue_steps = (
        db.query(ReportStep)
        .filter(
            ReportStep.completed_at.is_(None),
            ReportStep.due_date.isnot(None),
        )
        .options(joinedload(ReportStep.report).joinedload(Report.complaint))
        .all()
    )

    step_summaries = []
    for step in overdue_steps:
        complaint = step.report.complaint
        hours = _hours_overdue(step)
        level = _level_to_send(hours, step.escalation_count or 0) if hours else None
        recipients = _build_recipients(level, complaint) if level else []
        step_summaries.append({
            "step_id": step.id,
            "step_code": step.step_code,
            "due_date": step.due_date.isoformat() if step.due_date else None,
            "hours_overdue": round(hours, 2) if hours else None,
            "escalation_count": step.escalation_count,
            "next_level_to_send": level,
            "recipients": recipients,
            "complaint_ref": complaint.reference_number,
            "quality_manager_email": complaint.quality_manager_email,
            "plant_manager_email": complaint.plant_manager_email,
        })

    # Outbox summary
    outbox_counts = {}
    for status in ("pending", "sent", "failed", "abandoned"):
        count = db.query(EmailOutbox).filter(EmailOutbox.status == status).count()
        outbox_counts[status] = count

    # Recent failed outbox entries
    recent_failed = (
        db.query(EmailOutbox)
        .filter(EmailOutbox.status.in_(["failed", "abandoned"]))
        .order_by(EmailOutbox.created_at.desc())
        .limit(5)
        .all()
    )
    failed_details = [
        {
            "id": e.id,
            "step_id": e.step_id,
            "level": e.escalation_level,
            "attempts": e.attempts,
            "last_error": e.last_error,
            "next_retry_at": e.next_retry_at.isoformat() if e.next_retry_at else None,
            "status": e.status,
        }
        for e in recent_failed
    ]

    return {
        "scheduler_running": scheduler_ok,
        "smtp": smtp_info,
        "thresholds": _get_thresholds(),
        "overdue_steps_total": len(overdue_steps),
        "steps_needing_escalation": [s for s in step_summaries if s["next_level_to_send"]],
        "all_overdue_steps": step_summaries,
        "outbox_counts": outbox_counts,
        "recent_failed_outbox": failed_details,
    }


# ── 2. SMTP connectivity test ─────────────────────────────────────────────────

@router.post("/test-smtp")
def test_smtp(to: str):
    """
    Send a real test email to `to` using your configured SMTP.
    Returns success or the exact error message.
    Example: POST /debug/test-smtp?to=you@example.com
    """
    try:
        _send_sync(
            subject="[AVOCarbon Debug] SMTP Test",
            recipients=[to],
            body_html="<p>SMTP is working correctly from Azure.</p>",
            cc=None,
        )
        return {"status": "ok", "message": f"Email sent to {to}"}
    except smtplib.SMTPAuthenticationError as e:
        return {"status": "error", "type": "auth", "detail": str(e)}
    except smtplib.SMTPConnectError as e:
        return {"status": "error", "type": "connection", "detail": str(e)}
    except smtplib.SMTPRecipientsRefused as e:
        return {"status": "error", "type": "recipient_refused", "detail": str(e)}
    except socket.gaierror as e:
        return {"status": "error", "type": "dns_resolution", "detail": str(e)}
    except Exception as e:
        return {"status": "error", "type": type(e).__name__, "detail": str(e)}


# ── 3. Manual escalation trigger ─────────────────────────────────────────────

@router.post("/trigger-escalation")
def trigger_escalation(db: Session = Depends(get_db)):
    """
    Manually runs check_and_escalate_all() and returns a detailed
    per-step result — no logs needed to see what happened.
    """
    from app.services.escalation_service import _process_step

    steps = (
        db.query(ReportStep)
        .filter(
            ReportStep.completed_at.is_(None),
            ReportStep.due_date.isnot(None),
        )
        .options(joinedload(ReportStep.report).joinedload(Report.complaint))
        .all()
    )

    results = []
    for step in steps:
        complaint = step.report.complaint
        hours = _hours_overdue(step)
        level = _level_to_send(hours, step.escalation_count or 0) if hours else None

        entry = {
            "step_id": step.id,
            "step_code": step.step_code,
            "complaint_ref": complaint.reference_number,
            "hours_overdue": round(hours, 2) if hours else None,
            "escalation_count_before": step.escalation_count,
            "level_to_send": level,
            "outcome": None,
            "error": None,
        }

        if hours is None:
            entry["outcome"] = "skipped — not overdue"
        elif level is None:
            entry["outcome"] = "skipped — no new level"
        else:
            try:
                fired = _process_step(db, step)
                db.commit()
                entry["outcome"] = "email_queued_and_sent" if fired else "skipped"
                entry["escalation_count_after"] = step.escalation_count
            except Exception as exc:
                db.rollback()
                entry["outcome"] = "error"
                entry["error"] = str(exc)

        results.append(entry)

    return {
        "steps_evaluated": len(results),
        "results": results,
    }