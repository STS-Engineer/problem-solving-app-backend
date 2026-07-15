"""
app/services/email_intake_service.py
════════════════════════════════════
Lenient intake path for complaints received by email.

Flow
────
1. Agent POSTs a (possibly incomplete) email to /intake/email.
2. Dedup on source_message_id — a re-send returns the existing row.
3. Follow-up on a known conversation_id attaches instead of duplicating.
4. Otherwise store a pending_review row.
5. Notify the resolved plant's contacts (CQE + QM + PM + GM), or the
   configured fallback triage email when the plant is unknown.

Nothing here runs the strict ComplaintCreate validation — that happens only
later, at promotion time, when a human has completed the missing fields.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.email import _send_sync
from app.core.form_options import evaluate_completeness
from app.models.email_intake import EmailIntake
from app.models.enums import PlantEnum
from app.models.plant_contacts import PlantContact
from app.schemas.complaint import ComplaintCreate
from app.schemas.email_intake import EmailIntakeCreate
from app.services.complaint_service import ComplaintService

logger = logging.getLogger(__name__)


# ── Plant resolution ────────────────────────────────────────────────────────


def _resolve_plant(payload: EmailIntakeCreate) -> Optional[PlantEnum]:
    """
    Determine the plant from the explicit field or from extracted_data.
    Returns None when it cannot be mapped to a valid PlantEnum value.
    """
    if payload.detected_plant is not None:
        return payload.detected_plant

    raw = (payload.extracted_data or {}).get("avocarbon_plant")
    if not raw:
        return None
    try:
        return PlantEnum(str(raw).strip().upper())
    except ValueError:
        logger.info("intake: extracted plant %r is not a valid PlantEnum", raw)
        return None


def _resolve_recipients(db: Session, plant: Optional[PlantEnum]) -> list[str]:
    """
    Initial "new intake" notification recipients = the QM + PM of the site
    (the managers who triage and assign a CQT). CQE(s) are notified later,
    once the QM assigns them.

    Plant unknown OR no managers seeded → fallback triage email.
    """
    if plant is not None:
        contact = (
            db.query(PlantContact).filter(PlantContact.plant == plant).one_or_none()
        )
        if contact:
            recipients = contact.manager_recipients()
            if recipients:
                return recipients
            logger.warning(
                "intake: plant %s has a contacts row but no QM/PM — using fallback",
                plant,
            )
        else:
            logger.warning(
                "intake: no plant_contacts row for %s — using fallback", plant
            )

    fallback = settings.INTAKE_FALLBACK_EMAIL.strip()
    return [fallback] if fallback else []


# ── Notification ──────────────────────────────────────────────────────────────


def _build_notification(intake: EmailIntake) -> tuple[str, str]:
    ref = f"#{intake.id}"
    subject = f"[AVOCarbon] New email complaint {ref} — needs review"

    review_url = f"{settings.INTAKE_REVIEW_BASE_URL.rstrip('/')}/intake/{intake.id}"
    ed = intake.extracted_data or {}
    missing = ", ".join(intake.missing_fields or []) or "—"
    plant = intake.detected_plant.value if intake.detected_plant else "Unknown"

    def _row(label: str, value) -> str:
        return (
            f'<tr><td style="padding:6px 0;font-weight:700;color:#1A2332;width:38%;">'
            f'{label}</td><td style="padding:6px 0;color:#4A5568;">{value or "—"}</td></tr>'
        )

    body_html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:620px;margin:0 auto;
                background:#f9fafb;padding:28px;border-radius:10px;">
      <div style="background:#fff;border-radius:8px;padding:26px;
                  border-left:4px solid #1A73E8;box-shadow:0 2px 8px rgba(0,0,0,0.07);">
        <h2 style="margin:0 0 4px;color:#1A2332;font-size:18px;">New complaint received by email</h2>
        <p style="margin:0 0 18px;color:#8A95A8;font-size:13px;">Intake {ref} — status: pending review</p>

        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          {_row("From", f"{intake.sender_name or ''} &lt;{intake.sender_email or '—'}&gt;")}
          {_row("Subject", intake.subject)}
          {_row("Plant", plant)}
          {_row("Customer", ed.get("customer"))}
          {_row("Product type", ed.get("avocarbon_product_type"))}
          {_row("Defect", ed.get("defects"))}
          {_row("Missing data", missing)}
        </table>

        <div style="margin:22px 0 6px;">
          <a href="{review_url}"
             style="display:inline-block;background:#1A73E8;color:#fff;text-decoration:none;
                    padding:11px 22px;border-radius:6px;font-size:14px;font-weight:600;">
            Review &amp; complete
          </a>
        </div>

        <p style="margin:18px 0 0;font-size:12px;color:#8A95A8;border-top:1px solid #eee;padding-top:14px;">
          This complaint was extracted automatically from an email and needs a human to
          confirm the data before it enters the 8D workflow.
        </p>
      </div>
    </div>
    """
    return subject, body_html


def _build_cqe_assignment_email(
    intake: EmailIntake,
    *,
    created: bool,
    url: str,
    reference: Optional[str] = None,
    missing_fields: Optional[list[str]] = None,
) -> tuple[str, str]:
    """
    Email sent to the CQT once the QM assigns them.

    created=True  → the complaint was auto-created (all data complete); the link
                    opens the live complaint.
    created=False → data is incomplete; the link opens the completion form where
                    the CQT fills the missing fields and creates the complaint.
    """
    ref = f"#{intake.id}"
    ed = intake.extracted_data or {}

    if created:
        subject = f"[AVOCarbon] Complaint {reference or ref} created and assigned to you"
        headline = "A complaint has been created and assigned to you"
        intro = (
            "All required information was available, so the 8D complaint has been "
            "created automatically. Please open it, review the data, and start the "
            "8D process."
        )
        btn_label = "Open the complaint"
        btn_color = "#0B8A5B"
        missing_block = ""
    else:
        subject = f"[AVOCarbon] Complaint {ref} assigned — please complete missing data"
        headline = "You have been assigned a complaint — action needed"
        intro = (
            "A customer complaint received by email has been assigned to you. Some "
            "required information is missing, so please open the completion form, fill "
            "the missing fields, and submit to create the complaint."
        )
        btn_label = "Complete &amp; create the complaint"
        btn_color = "#E8710A"
        pretty = ", ".join((missing_fields or [])) or "—"
        missing_block = (
            f'<p style="margin:14px 0 0;font-size:13px;color:#C97B0A;">'
            f"<b>Missing fields:</b> {pretty}</p>"
        )

    body_html = f"""
    <div style="font-family:Inter,Arial,sans-serif;max-width:620px;margin:0 auto;
                background:#f9fafb;padding:28px;border-radius:10px;">
      <div style="background:#fff;border-radius:8px;padding:26px;
                  border-left:4px solid {btn_color};box-shadow:0 2px 8px rgba(0,0,0,0.07);">
        <h2 style="margin:0 0 4px;color:#1A2332;font-size:18px;">{headline}</h2>
        <p style="margin:0 0 18px;color:#8A95A8;font-size:13px;">Intake {ref} — assigned by {intake.assigned_by or "your QM"}</p>
        <p style="margin:0 0 14px;color:#4A5568;font-size:14px;">{intro}</p>
        <table style="width:100%;border-collapse:collapse;font-size:13px;color:#4A5568;">
          <tr><td style="padding:6px 0;font-weight:700;color:#1A2332;width:38%;">Subject</td>
              <td style="padding:6px 0;">{intake.subject or "—"}</td></tr>
          <tr><td style="padding:6px 0;font-weight:700;color:#1A2332;">Customer</td>
              <td style="padding:6px 0;">{ed.get("customer") or "—"}</td></tr>
        </table>
        {missing_block}
        <div style="margin:22px 0 6px;">
          <a href="{url}"
             style="display:inline-block;background:{btn_color};color:#fff;text-decoration:none;
                    padding:11px 22px;border-radius:6px;font-size:14px;font-weight:600;">
            {btn_label}
          </a>
        </div>
      </div>
    </div>
    """
    return subject, body_html


def _notify(intake: EmailIntake, recipients: list[str]) -> None:
    if not recipients:
        logger.error("intake %s: no recipients resolved — notification skipped", intake.id)
        return
    subject, body_html = _build_notification(intake)
    try:
        _send_sync(subject=subject, recipients=recipients, body_html=body_html, cc=None)
        logger.info("intake %s: notified %s", intake.id, recipients)
    except Exception as exc:  # best-effort — the intake is already stored
        logger.warning("intake %s: notification failed: %s", intake.id, exc)


# ── Public entry point ──────────────────────────────────────────────────────


class EmailIntakeService:

    @staticmethod
    def ingest(db: Session, payload: EmailIntakeCreate) -> tuple[EmailIntake, str]:
        """
        Idempotent intake. Returns (intake, status) where status is one of:
        'created' | 'duplicate' | 'attached_to_existing'.
        """
        # ── 1. Dedup on message id ──────────────────────────────────────────
        existing = (
            db.query(EmailIntake)
            .filter(EmailIntake.source_message_id == payload.source_message_id)
            .one_or_none()
        )
        if existing:
            logger.info(
                "intake: duplicate message_id %s -> intake %s",
                payload.source_message_id,
                existing.id,
            )
            return existing, "duplicate"

        # ── 2. Thread follow-up → attach to the open intake on that thread ──
        if payload.conversation_id:
            thread = (
                db.query(EmailIntake)
                .filter(
                    EmailIntake.conversation_id == payload.conversation_id,
                    EmailIntake.status.in_(("pending_review", "promoted")),
                )
                .order_by(EmailIntake.created_at.desc())
                .first()
            )
            if thread:
                followups = list(thread.attachments or [])
                followups.append(
                    {
                        "type": "followup_email",
                        "source_message_id": payload.source_message_id,
                        "subject": payload.subject,
                        "received_at": payload.received_at.isoformat()
                        if payload.received_at
                        else None,
                        "raw_body": payload.raw_body,
                    }
                )
                thread.attachments = followups
                thread.updated_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(thread)
                logger.info(
                    "intake: follow-up on conversation %s attached to intake %s",
                    payload.conversation_id,
                    thread.id,
                )
                return thread, "attached_to_existing"

        # ── 3. Store a new pending_review row ───────────────────────────────
        plant = _resolve_plant(payload)
        intake = EmailIntake(
            source_message_id=payload.source_message_id,
            conversation_id=payload.conversation_id,
            sender_email=payload.sender_email,
            sender_name=payload.sender_name,
            subject=payload.subject,
            received_at=payload.received_at,
            raw_body=payload.raw_body,
            raw_html=payload.raw_html,
            attachments=[a.model_dump() for a in payload.attachments],
            extracted_data=payload.extracted_data or {},
            ai_notes=payload.ai_notes,
            missing_fields=payload.missing_fields or [],
            detected_plant=plant,
            status="pending_review",
        )
        db.add(intake)
        db.commit()
        db.refresh(intake)

        # ── 4. Notify (plant contacts or fallback) ──────────────────────────
        recipients = _resolve_recipients(db, plant)
        _notify(intake, recipients)
        intake.notified_to = recipients
        db.commit()
        db.refresh(intake)

        logger.info(
            "intake %s: created (plant=%s, recipients=%s)",
            intake.id,
            plant.value if plant else "unknown",
            recipients,
        )
        return intake, "created"

    @staticmethod
    def set_plant(
        db: Session,
        intake_id: int,
        plant: PlantEnum,
        renotify: bool = True,
    ) -> EmailIntake:
        """
        Triage: set/correct the responsible plant on an intake. When renotify is
        True, notify that plant's QM/PM (used when the plant was Unknown and a
        triager — e.g. Esperanza — has now identified the site).
        """
        intake = db.get(EmailIntake, intake_id)
        if intake is None:
            raise ValueError(f"intake {intake_id} not found")

        intake.detected_plant = plant
        db.commit()
        db.refresh(intake)

        if renotify:
            recipients = _resolve_recipients(db, plant)
            _notify(intake, recipients)
            merged = list(intake.notified_to or []) + recipients
            intake.notified_to = PlantContact._dedup(merged)
            db.commit()
            db.refresh(intake)
            logger.info(
                "intake %s: plant set to %s, re-notified %s",
                intake.id,
                plant.value,
                recipients,
            )
        return intake

    @staticmethod
    def assign_cqe(
        db: Session,
        intake_id: int,
        cqe_email: str,
        assigned_by: Optional[str] = None,
    ) -> EmailIntake:
        """
        QM assigns a CQT (internal Customer Quality Engineer) to an intake and
        notifies them with a link to complete the complaint.
        """
        intake = db.get(EmailIntake, intake_id)
        if intake is None:
            raise ValueError(f"intake {intake_id} not found")

        cqe_email = (cqe_email or "").strip()
        if not cqe_email:
            raise ValueError("cqe_email is required")

        intake.assigned_cqe_email = cqe_email
        intake.assigned_by = (assigned_by or "").strip() or None
        intake.assigned_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(intake)

        # Try to auto-create the complaint (path B) if all data is complete;
        # otherwise the CQT is sent to the completion form (path A).
        result = EmailIntakeService.promote(db, intake_id)
        base = settings.INTAKE_REVIEW_BASE_URL.rstrip("/")

        if result["status"] in ("created", "already_promoted"):
            url = f"{base}/8d/{result['reference_number']}/D1"
            subject, body = _build_cqe_assignment_email(
                intake, created=True, url=url, reference=result["reference_number"]
            )
        else:  # incomplete → completion form
            url = f"{base}/intake/{intake.id}/complete"
            subject, body = _build_cqe_assignment_email(
                intake, created=False, url=url, missing_fields=result["missing_fields"]
            )

        try:
            _send_sync(subject=subject, recipients=[cqe_email], body_html=body, cc=None)
            logger.info(
                "intake %s: CQT %s assigned & notified (%s)",
                intake.id,
                cqe_email,
                result["status"],
            )
        except Exception as exc:  # best-effort — assignment is already saved
            logger.warning(
                "intake %s: CQT assignment saved but notification failed: %s",
                intake.id,
                exc,
            )

        db.refresh(intake)
        return intake

    # ── Promotion (intake → real complaint) ────────────────────────────────

    @staticmethod
    def _create_complaint_from_intake(db: Session, intake: EmailIntake, normalized: dict):
        """Build a validated ComplaintCreate from normalized data and create it."""
        plant = normalized.get("avocarbon_plant")
        contact = None
        if plant:
            contact = (
                db.query(PlantContact)
                .filter(PlantContact.plant == plant)
                .one_or_none()
            )

        payload = ComplaintCreate(
            complaint_name=normalized["complaint_name"],
            quality_issue_warranty=normalized.get("quality_issue_warranty"),
            customer=normalized.get("customer"),
            customer_plant_name=normalized.get("customer_plant_name"),
            avocarbon_plant=normalized.get("avocarbon_plant"),
            avocarbon_product_type=normalized.get("avocarbon_product_type"),
            potential_avocarbon_process_linked_to_problem=normalized.get(
                "potential_avocarbon_process_linked_to_problem"
            ),
            product_line=normalized["product_line"],
            concerned_application=normalized.get("concerned_application"),
            customer_complaint_date=normalized.get("customer_complaint_date"),
            complaint_opening_date=date.today(),
            complaint_description=normalized.get("complaint_description"),
            defects=normalized.get("defects"),
            repetitive_complete_with_number=normalized.get(
                "repetitive_complete_with_number"
            )
            or "0",
            cqt_email=intake.assigned_cqe_email,
            quality_manager_emails=(
                list(contact.quality_manager_emails or []) if contact else []
            ),
            plant_manager_email=(contact.plant_manager_email if contact else None),
            reported_by=None,
        )
        return ComplaintService.create_complaint(db=db, payload=payload)

    @staticmethod
    def promote(db: Session, intake_id: int) -> dict:
        """
        Idempotent promotion. Returns a dict:
          status ∈ {created, already_promoted, incomplete}
          complaint_id, reference_number, missing_fields
        Only creates a complaint when ALL required fields are present & valid.
        """
        intake = db.get(EmailIntake, intake_id)
        if intake is None:
            raise ValueError(f"intake {intake_id} not found")

        if intake.complaint_id:
            ref = intake.complaint.reference_number if intake.complaint else None
            return {
                "status": "already_promoted",
                "complaint_id": intake.complaint_id,
                "reference_number": ref,
                "missing_fields": [],
            }

        normalized, missing = evaluate_completeness(intake.extracted_data)
        if missing:
            return {
                "status": "incomplete",
                "complaint_id": None,
                "reference_number": None,
                "missing_fields": missing,
            }

        complaint = EmailIntakeService._create_complaint_from_intake(db, intake, normalized)
        intake.status = "promoted"
        intake.complaint_id = complaint.id
        db.commit()
        db.refresh(intake)
        logger.info(
            "intake %s promoted -> complaint %s (%s)",
            intake.id,
            complaint.id,
            complaint.reference_number,
        )
        return {
            "status": "created",
            "complaint_id": complaint.id,
            "reference_number": complaint.reference_number,
            "missing_fields": [],
        }

    @staticmethod
    def complete(db: Session, intake_id: int, payload: ComplaintCreate) -> dict:
        """
        Path A: the CQT submits the completion form. Creates the complaint from
        the (human-completed) payload and marks the intake promoted. Idempotent.
        """
        intake = db.get(EmailIntake, intake_id)
        if intake is None:
            raise ValueError(f"intake {intake_id} not found")

        if intake.complaint_id:
            ref = intake.complaint.reference_number if intake.complaint else None
            return {
                "status": "already_promoted",
                "complaint_id": intake.complaint_id,
                "reference_number": ref,
            }

        # Auto-fill from the plant + assignment (QM/PM automatic, CQT = assigned)
        updates: dict = {}
        if not payload.cqt_email and intake.assigned_cqe_email:
            updates["cqt_email"] = intake.assigned_cqe_email
        if payload.avocarbon_plant:
            contact = (
                db.query(PlantContact)
                .filter(PlantContact.plant == payload.avocarbon_plant)
                .one_or_none()
            )
            if contact:
                if not payload.quality_manager_emails and contact.quality_manager_emails:
                    updates["quality_manager_emails"] = list(
                        contact.quality_manager_emails
                    )
                if not payload.plant_manager_email and contact.plant_manager_email:
                    updates["plant_manager_email"] = contact.plant_manager_email

        data = payload.model_copy(update=updates) if updates else payload
        complaint = ComplaintService.create_complaint(db=db, payload=data)
        intake.status = "promoted"
        intake.complaint_id = complaint.id
        db.commit()
        db.refresh(intake)
        logger.info("intake %s completed -> complaint %s", intake.id, complaint.id)
        return {
            "status": "created",
            "complaint_id": complaint.id,
            "reference_number": complaint.reference_number,
        }
