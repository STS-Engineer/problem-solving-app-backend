from datetime import datetime, timedelta, timezone
from typing import List, Optional
import random
import string
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.schemas.complaint import ComplaintCreate, ComplaintUpdate
# from app.services import webhook_service
from app.services.auto_extraction import auto_fill_from_complaint
from app.services.escalation_service import _SCALE
from app.services.utils.report_helpers import generate_report_number, get_8d_steps_definitions
import logging
logger = logging.getLogger(__name__)

def generate_complaint_number():
    return "C-" + "".join(random.choices(string.digits, k=5))

# ── SLA days per step code ─────────────────────────────────────────────────────
# Matches the escalation ladder: overdue = now > due_date.
# Adjust these to your company's actual SLA commitments.
_STEP_SLA_DAYS: dict[str, int] = {
    "D1":  1,
    "D2":  3,
    "D3":  2,   
    "D4":  5,
    "D5":  10,
    "D6":  30,
    "D7":  45,
    "D8":  60,
}

def _due_date_for_step(step_code: str, created_at: datetime) -> datetime | None:
    """
    Return the due_date for a step given the complaint creation timestamp.
    Returns None for unknown step codes (safe fallback — scheduler skips them).
    """
    days = _STEP_SLA_DAYS.get(step_code)
    if days is None:
        return None
    delta_hours = days * 24 * _SCALE
    return created_at + timedelta(hours=delta_hours)

PRIORITY_MAPPING = {
    "CS2": "critical",
    "CS1": "high",
    "WR": "medium",
    "Quality Alert": "low",
}

class ComplaintService:
    @staticmethod
    def create_complaint(
        db: Session, 
        payload: ComplaintCreate,
    ) -> tuple[Complaint, list[int]]:        
        """
        Crée une plainte et optionnellement initialise un rapport 8D
        
        Args:
            db: Session de base de données
            payload: Données de la plainte
            create_report: Si True, crée automatiquement un rapport 8D
            report_title: Titre du rapport (optionnel, sinon utilise le nom de la plainte)
        """
        # ── 1. Create complaint ───────────────────────────────────
        complaint = Complaint(**payload.model_dump())
        complaint.reference_number = generate_complaint_number()
        complaint.priority = PRIORITY_MAPPING.get(payload.quality_issue_warranty, "low")

        created_at = datetime.now(timezone.utc)
        if not complaint.due_date:
            complaint.due_date = created_at + timedelta(days=30)

        db.add(complaint)
        db.flush()

        # ── 2. Create report ──────────────────────────────────────
        report = Report(
            complaint_id=complaint.id,
            report_number=generate_report_number(),
            title=f"8D Report - {complaint.complaint_name}",
            plant=complaint.avocarbon_plant,
            created_by=complaint.reported_by,
            status="draft",
        )
        db.add(report)
        db.flush()

        # ── 3. Create 8 steps ─────────────────────────────────────
        steps_definitions = get_8d_steps_definitions()
        step_ids: list[int] = []

        for step_def in steps_definitions:
            step_code: str = step_def["code"]
            due_date = _due_date_for_step(step_code, created_at)

            step = ReportStep(
                report_id=report.id,
                step_code=step_code,
                step_name=step_def["name"],
                status="not_started",
                data={},
                due_date=due_date,
            )
            db.add(step)
            db.flush()
            step_ids.append(step.id)

        db.commit()
        db.refresh(complaint)

        # IMPORTANT: no OpenAI call here anymore
        return complaint, step_ids
        
        # 4. Send webhook notification (async, non-blocking)
        # webhook_service.send_webhook_background(
        #     event_type="complaint.created",
        #     complaint_data={
        #         "id": complaint.id,
        #         "reference_number": complaint.reference_number,
        #         "complaint_name": complaint.complaint_name,
        #         "status": complaint.status,
        #         "severity": complaint.severity,
        #         "priority": complaint.priority,
        #         "due_date": complaint.due_date.isoformat() if complaint.due_date else None,
        #         "created_at": complaint.created_at.isoformat(),
        #         "updated_at": complaint.updated_at.isoformat(),
        #         "customer": complaint.customer,
        #         "product_line": complaint.product_line.value if complaint.product_line else None
        #     },
        #     complaint_id=complaint.id,
        #     db=db
        # )
    @staticmethod
    def run_autofill_task(complaint_id: int, step_ids: list[int]) -> None:
        db = SessionLocal()
        try:
            complaint = db.get(Complaint, complaint_id)
            if not complaint:
                logger.warning("autofill: complaint not found id=%s", complaint_id)
                return

            steps = [db.get(ReportStep, sid) for sid in step_ids]
            steps = [s for s in steps if s is not None]
            if not steps:
                logger.warning("autofill: no steps found for complaint_id=%s", complaint_id)
                return

            extracted = auto_fill_from_complaint(db, complaint, steps)

            if extracted:
                db.commit()
            else:
                db.rollback()

        except Exception:
            logger.exception("autofill: failed complaint_id=%s", complaint_id)
            try:
                db.rollback()
            except Exception:
                pass
        finally:
            db.close()
    @staticmethod
    def get_complaint_by_id(db: Session, complaint_id: int) -> Optional[Complaint]:
        """Récupère une plainte par son ID"""
        return db.query(Complaint).filter(Complaint.id == complaint_id).first()
    
    @staticmethod
    def get_complaint_by_reference(db: Session, reference_number: str) -> Optional[Complaint]:
        """Récupère une plainte par son numéro de référence"""
        return db.query(Complaint).filter(
            Complaint.reference_number == reference_number
        ).first()
    
    @staticmethod
    def list_complaints(
        db: Session,
        skip: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        product_line: Optional[str] = None,
    ) -> List[Complaint]:
        query = db.query(Complaint)
        
        if status:
            query = query.filter(Complaint.status == status)
        if product_line:
            query = query.filter(Complaint.product_line == product_line)
        
        return query.order_by(Complaint.created_at.desc()).offset(skip).limit(limit).all()

    @staticmethod
    def update_complaint(
        db: Session, complaint_id: int, payload: ComplaintUpdate
    ) -> Optional[Complaint]:
        complaint = ComplaintService.get_complaint_by_id(db, complaint_id)
        if not complaint:
            return None

        data = payload.model_dump(exclude_unset=True)

        #Track if status changed to closed
        status_changed_to_closed = False
        if 'status' in data and data['status'] == 'closed' and complaint.status != 'closed':
            status_changed_to_closed = True
            data['closed_at'] = datetime.now(timezone.utc)

        for key, value in data.items():
            setattr(complaint, key, value)

        db.add(complaint)
        db.commit()
        db.refresh(complaint)
        # Send webhook notification for updates
        event_type = "complaint.closed" if status_changed_to_closed else "complaint.updated"
        
        # webhook_service.send_webhook_background(
        #     event_type=event_type,
        #     complaint_data={
        #         "id": complaint.id,
        #         "reference_number": complaint.reference_number,
        #         "complaint_name": complaint.complaint_name,
        #         "status": complaint.status,
        #         "severity": complaint.severity,
        #         "priority": complaint.priority,
        #         "due_date": complaint.due_date.isoformat() if complaint.due_date else None,
        #         "closed_at": complaint.closed_at.isoformat() if complaint.closed_at else None,
        #         "created_at": complaint.created_at.isoformat(),
        #         "updated_at": complaint.updated_at.isoformat(),
        #         "customer": complaint.customer,
        #         "product_line": complaint.product_line.value if complaint.product_line else None
        #     },
        #     complaint_id=complaint.id,
        #     db=db
        # )

        return complaint

    @staticmethod
    def delete_complaint(db: Session, complaint_id: int) -> bool:
        complaint = ComplaintService.get_complaint_by_id(db, complaint_id)
        if not complaint:
            return False

        db.delete(complaint)
        db.commit()
        return True
    
    @staticmethod
    def get_complaints_for_sync(
            db: Session,
            since: Optional[datetime] = None
        ) -> List[Complaint]:
            """
            Get complaints for polling/sync endpoint
            Returns:
            - Complaints created/updated after 'since'
            - Complaints that are overdue (status != closed and due_date < now)
            - Complaints where webhook failed (webhook_sent = False)
            """
            now = datetime.now(timezone.utc)
            
            if since is None:
                since = now - timedelta(hours=24)  # Default: last 24 hours
            
            query = db.query(Complaint).filter(
                or_(
                    # New or updated complaints
                    Complaint.created_at >= since,
                    Complaint.updated_at >= since,
                    # Overdue open complaints
                    and_(
                        Complaint.status != 'closed',
                        Complaint.due_date < now
                    ),
                    # Failed webhooks
                    Complaint.webhook_sent == False
                )
            )
            
            return query.order_by(Complaint.created_at.desc()).all()
