from datetime import datetime, timedelta, timezone
from typing import List, Optional
import random
import string
from sqlalchemy import and_, or_,select, func, case

from sqlalchemy.orm import Session

from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.schemas.complaint import ComplaintCreate, ComplaintUpdate
# from app.services import webhook_service
from app.services.auto_extraction import auto_fill_from_complaint
from app.services.utils.report_helpers import generate_report_number, get_8d_steps_definitions
# from app.services.webhook_service import enqueue_complaint_created,enqueue_type_updated

def generate_complaint_number():
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    year = datetime.now().year
    return f"CMP-{year}-{suffix}"

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
    delta_hours = days * 24 
    return created_at + timedelta(hours=delta_hours)

PRIORITY_MAPPING = {
    "CS2": "critical",
    "CS1": "high",
    "WR": "medium",
    "Quality Alert": "low",
}
_STEP_ORDER = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]

# Postgres CASE WHEN ordering for step codes
_STEP_PRIORITY = case(
    *[(ReportStep.step_code == code, i) for i, code in enumerate(_STEP_ORDER)],
    else_=99
)
class ComplaintService:
    @staticmethod
    def create_complaint(
        db: Session, 
        payload: ComplaintCreate,
    ) -> Complaint:
        """
        Crée une plainte et optionnellement initialise un rapport 8D
        
        Args:
            db: Session de base de données
            payload: Données de la plainte
            create_report: Si True, crée automatiquement un rapport 8D
            report_title: Titre du rapport (optionnel, sinon utilise le nom de la plainte)
        """
        # ── 1. Create complaint  ───────────────────────────────────
        complaint = Complaint(**payload.model_dump())
        complaint.reference_number = generate_complaint_number()
        complaint.priority = PRIORITY_MAPPING.get(payload.quality_issue_warranty, "low")
        created_at = datetime.now(timezone.utc)
        if not complaint.due_date:
            complaint.due_date = created_at + timedelta(days=30)
        db.add(complaint)
        db.flush()

        # ── 2. Create report (UNCHANGED) ──────────────────────────────────────
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

        # ── 3. Create 8 steps — collect the step objects now  ← SMALL CHANGE ─
        # (original used a loop without keeping references; we keep them for auto-fill)
        steps_definitions = get_8d_steps_definitions()
        created_steps = []                                        # ← ADD

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
            db.flush()                                            # ← ADD (get step.id)
            created_steps.append(step)                           # ← ADD


        # enqueue_complaint_created
        db.commit()
        db.refresh(complaint)

        # ── 4. Auto-fill step data from complaint description  ← ADD BLOCK ───
        #
        # Runs AFTER commit so all step IDs exist in the DB.
        # Non-fatal: a failure here never rolls back the complaint.
        try:
            auto_fill_from_complaint(db, complaint, created_steps)  
            db.commit()                                            
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning(
                "auto_fill failed for %s — continuing without pre-fill: %s",
                complaint.reference_number, exc,
            )
            try:
                db.rollback()
            except Exception:
                pass
        # ── end ADD block ─────────────────────────────────────────────────────

        return complaint
        
    
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
        cqt_email: Optional[str] = None,
    ) -> List[Complaint]:
        # ── Subquery: first non-fulfilled step code per report ─────────────────
        # Uses a correlated subquery ordered by D1→D8 priority.
        # Returns NULL when all steps are fulfilled (= all_completed).
        first_open_step = (
            select(ReportStep.step_code)
            .where(
                and_(
                    ReportStep.report_id == Report.id,
                    ReportStep.status != "fulfilled",
                )
            )
            .order_by(_STEP_PRIORITY)
            .limit(1)
            .correlate(Report)
            .scalar_subquery()
        )

        # ── Subquery: does this complaint have ALL 8 steps fulfilled? ──────────
        fulfilled_count = (
            select(func.count())
            .where(
                and_(
                    ReportStep.report_id == Report.id,
                    ReportStep.status == "fulfilled",
                )
            )
            .correlate(Report)
            .scalar_subquery()
        )

        # ── Main query: complaints LEFT JOIN reports ───────────────────────────
        q = (
            db.query(
                Complaint,
                Report.id.label("report_id"),
                Report.report_number.label("report_number"),
                first_open_step.label("first_open_step"),
                fulfilled_count.label("fulfilled_count"),
            )
            .outerjoin(Report, Report.complaint_id == Complaint.id)
        )

        if status:
            q = q.filter(Complaint.status == status)
        if product_line:
            q = q.filter(Complaint.product_line == product_line)
        if cqt_email:
            q = q.filter(Complaint.cqt_email.ilike(f"%{cqt_email}%"))

        rows = (
            q.order_by(Complaint.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

        # ── Build result list ─────────────────────────────────────────────────
        results = []
        for complaint, report_id, report_number, first_open, fulfilled in rows:
            has_report      = report_id is not None
            all_completed   = has_report and first_open is None and fulfilled == 8
            has_export      = all_completed

            # current_step_code logic mirrors your existing get_current_step endpoint
            if not has_report:
                current_step_code = (complaint.status or "open").upper()
            elif all_completed:
                current_step_code = "D8"
            else:
                current_step_code = first_open  # e.g. "D3"

            # export_filename only computed when ready (avoids a query)
            export_filename = None
            if has_export and report_number:
                name = (complaint.complaint_name or "")[:40]
                name = name.replace(" ", "_").replace("/", "-")
                export_filename = f"8D_{report_number}_{name}.xlsx"

            # Attach computed attrs so Pydantic's from_attributes picks them up
            setattr(complaint, "has_export_report",  has_export)
            setattr(complaint, "export_filename",     export_filename)
            setattr(complaint, "current_step_code",   current_step_code)
            setattr(complaint, "all_completed",        all_completed)
            setattr(complaint, "has_report",           has_report)
            results.append(complaint)

        return results
    @staticmethod
    def update_complaint(
        db: Session, complaint_id: int, payload: ComplaintUpdate
    ) -> Optional[Complaint]:
        complaint = ComplaintService.get_complaint_by_id(db, complaint_id)
        if not complaint:
            return None
        old_type=complaint.quality_issue_warranty
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
        new_type=payload.quality_issue_warranty
        #if old_type != new_type:
            #enqueue_type_updated(db, complaint, old_type, new_type)
        #TODO we must handle case of status changes

        # Send webhook notification for updates
        #event_type = "complaint.closed" if status_changed_to_closed else "complaint.updated"
        
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
