"""
app/services/intake_escalation_service.py

Escalation for email intakes that have NOT yet entered the complaint list.

An intake sits in one of two "pre-complaint" stages that nobody was chasing:

  stage 'awaiting_cqt'        received, no CQT assigned yet   → chase QM/PM
  stage 'awaiting_complaint'  CQT assigned, complaint not     → chase the CQT
                              created yet

Once the intake is promoted (complaint_id set), the normal complaint/step
escalation (escalation_service.py) takes over and this service ignores it.

────────────────────────────────────────────────────────────────────────────
⚠️  PROVISIONAL RULES — thresholds and the recipient ladder below are placeholders
    to be confirmed with the client. Everything is centralised in this block and
    overridable via env vars, so finalising the policy is a one-place change.
────────────────────────────────────────────────────────────────────────────

Env overrides (comma-separated hours, one per level L1,L2,L3):
    INTAKE_ESC_HOURS_AWAITING_CQT        default "8,24,48"
    INTAKE_ESC_HOURS_AWAITING_COMPLAINT  default "4,8,24"
    TEST_ESCALATION=true                 → minutes instead of hours (2,4,6)

Design mirrors escalation_service.py: synchronous, safe to run from the
BackgroundScheduler or a manual trigger endpoint.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email import _send_sync as _send_email
from app.models.email_intake import EmailIntake
from app.models.plant_contacts import PlantContact
from app.services.email_templates import build_intake_escalation_email

logger = logging.getLogger(__name__)

STAGE_AWAITING_CQT = "awaiting_cqt"
STAGE_AWAITING_COMPLAINT = "awaiting_complaint"

# Highest level defined by the ladders below.
_MAX_LEVEL = 3

# Final escalation recipient for stage 1 (COO). Overridable via env.
_ESCALATION_FINAL_EMAIL = os.getenv("COO_EMAIL", "roberto.gonzalez@avocarbon.com")


# ── Thresholds (PROVISIONAL) ────────────────────────────────────────────────


def _is_test_mode() -> bool:
    return os.getenv("TEST_ESCALATION", "false").lower() == "true"


def _parse_hours(env_key: str, default: str) -> list[float]:
    raw = os.getenv(env_key, default)
    out: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError:
            logger.warning(
                "intake-escalation: bad hour %r in %s — ignored", part, env_key
            )
    return out


def _thresholds(stage: str) -> list[tuple[float, int]]:
    """Return [(hours_overdue, level), …] ascending for the given stage."""
    if _is_test_mode():
        m = 1 / 60  # one minute expressed in hours
        hours = [2 * m, 4 * m, 6 * m]
    elif stage == STAGE_AWAITING_CQT:
        hours = _parse_hours("INTAKE_ESC_HOURS_AWAITING_CQT", "8,24,48")
    else:
        hours = _parse_hours("INTAKE_ESC_HOURS_AWAITING_COMPLAINT", "4,8,24")
    return [(h, i + 1) for i, h in enumerate(hours)]


# ── Recipient ladder (PROVISIONAL) ──────────────────────────────────────────


def _recipients(
    stage: str, level: int, intake: EmailIntake, contact: PlantContact | None
) -> list[str]:
    """
    Who receives the reminder at a given stage/level. Falls back to the triage
    email (INTAKE_FALLBACK_EMAIL) when the plant / its contacts are unknown.
    """
    qm = list(contact.quality_manager_emails or []) if contact else []
    pm = (
        [contact.plant_manager_email] if contact and contact.plant_manager_email else []
    )
    final = [_ESCALATION_FINAL_EMAIL] if _ESCALATION_FINAL_EMAIL else []
    cqt = [intake.assigned_cqe_email] if intake.assigned_cqe_email else []

    if stage == STAGE_AWAITING_CQT:
        # L1 QM(s) → L2 +PM → L3 +Roberto Gonzalez (COO)
        ladder = {1: qm, 2: qm + pm, 3: qm + pm + final}
    else:  # awaiting_complaint
        # L1 CQT → L2 +QM(s) → L3 +PM
        ladder = {1: cqt, 2: cqt + qm, 3: cqt + qm + pm}

    recips = [e for e in ladder.get(level, []) if e]
    if not recips:
        fb = (settings.INTAKE_FALLBACK_EMAIL or "").strip()
        recips = [fb] if fb else []
    return PlantContact._dedup(recips)


# ── Stage / timing helpers ──────────────────────────────────────────────────


def _current_stage(intake: EmailIntake) -> str | None:
    """The stage to chase, or None if the intake is resolved / not chaseable."""
    if intake.complaint_id is not None:
        return None  # already a complaint — step escalation handles it
    if intake.status != "pending_review":
        return None  # rejected / duplicate / promoted
    if not intake.assigned_cqe_email:
        return STAGE_AWAITING_CQT
    return STAGE_AWAITING_COMPLAINT


def _anchor(intake: EmailIntake, stage: str) -> datetime | None:
    """The timestamp the stage is measured from."""
    ts = intake.assigned_at if stage == STAGE_AWAITING_COMPLAINT else intake.created_at
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _level_to_send(hours: float, already_sent: int, stage: str) -> int | None:
    thresholds = _thresholds(stage)
    triggered = max((lvl for thr, lvl in thresholds if hours >= thr), default=0)
    if triggered == 0:
        return None
    next_level = already_sent + 1
    return next_level if next_level <= triggered else None


# ── Main job ────────────────────────────────────────────────────────────────


def check_and_escalate_intakes(db: Session) -> int:
    """
    Scan un-promoted intakes and send stage-appropriate reminders.
    Returns the number of reminders sent. Safe to call from the scheduler or a
    manual trigger endpoint.
    """
    intakes = (
        db.query(EmailIntake)
        .filter(
            EmailIntake.complaint_id.is_(None),
            EmailIntake.status == "pending_review",
        )
        .all()
    )
    logger.info("Intake escalation scan: %d un-promoted intake(s)", len(intakes))

    fired = 0
    for intake in intakes:
        try:
            if _process_intake(db, intake):
                db.commit()
                fired += 1
            else:
                # stage/count may have been reset without a send — persist it
                db.commit()
        except Exception:
            logger.exception(
                "Intake escalation error on intake_id=%s — rolling back, continuing",
                intake.id,
            )
            db.rollback()

    logger.info("Intake escalation scan complete — %d reminder(s) sent", fired)
    return fired


def _process_intake(db: Session, intake: EmailIntake) -> bool:
    """Evaluate one intake. Returns True if a reminder was sent."""
    stage = _current_stage(intake)
    if stage is None:
        return False

    # Reset the per-stage counter when the stage changes (e.g. CQT just assigned).
    if intake.escalation_stage != stage:
        intake.escalation_stage = stage
        intake.escalation_count = 0

    anchor = _anchor(intake, stage)
    if anchor is None:
        return False

    hours = (datetime.now(timezone.utc) - anchor).total_seconds() / 3600
    if hours <= 0:
        return False

    level = _level_to_send(hours, intake.escalation_count or 0, stage)
    if level is None:
        return False

    contact = None
    if intake.detected_plant is not None:
        contact = (
            db.query(PlantContact)
            .filter(PlantContact.plant == intake.detected_plant)
            .one_or_none()
        )

    recipients = _recipients(stage, level, intake, contact)
    if not recipients:
        logger.warning(
            "Intake %s: stage=%s L%s due but NO RECIPIENTS — skipping",
            intake.id,
            stage,
            level,
        )
        return False

    subject, body = build_intake_escalation_email(
        intake_id=intake.id,
        stage=stage,
        level=level,
        hours_waiting=hours,
        sender_email=intake.sender_email,
        subject_line=intake.subject,
        plant=intake.detected_plant.value if intake.detected_plant else None,
        assigned_cqe_email=intake.assigned_cqe_email,
        review_base_url=settings.INTAKE_REVIEW_BASE_URL,
        test_mode=_is_test_mode(),
    )

    _send_email(subject=subject, recipients=recipients, body_html=body, cc=None)

    now = datetime.now(timezone.utc)
    intake.escalation_count = level
    intake.escalation_sent_at = now
    intake.escalation_log = list(intake.escalation_log or []) + [
        {
            "stage": stage,
            "level": level,
            "recipients": recipients,
            "sent_at": now.isoformat(),
            "hours_waiting": round(hours, 2),
        }
    ]
    logger.info(
        "Intake %s: sent stage=%s L%s reminder to %s (waited %.1fh)",
        intake.id,
        stage,
        level,
        recipients,
        hours,
    )
    return True
