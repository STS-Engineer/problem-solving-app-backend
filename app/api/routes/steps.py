# app/api/routes/steps.py

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.complaint import Complaint
from app.services.step_service import StepService
from app.schemas.step_data import *
from app.models.report import Report
from app.models.report_step import ReportStep

router = APIRouter()


@router.get("/complaint/{reference_number}/step/{step_code}")
def get_step_by_complaint_and_code(
    reference_number: str,  # ← was complaint_id: int
    step_code: str,
    db: Session = Depends(get_db),
):
    if step_code not in ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]:
        raise HTTPException(status_code=400, detail="Invalid step code")

    # First resolve reference_number → complaint → report
    complaint = (
        db.query(Complaint)
        .filter(Complaint.reference_number == reference_number)
        .first()
    )
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    report = (
        db.query(Report)
        .filter(
            Report.complaint_id == complaint.id  # internal id still used for DB joins
        )
        .first()
    )
    if not report:
        raise HTTPException(
            status_code=404, detail="No 8D report found for this complaint"
        )

    step = StepService.get_step_by_code(db, report.id, step_code)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    return step


@router.get("/complaint/{complaint_id}/steps")
def list_steps_by_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
):
    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(
            status_code=404, detail="No 8D report found for this complaint"
        )

    steps = (
        db.query(ReportStep)
        .filter(ReportStep.report_id == report.id)
        .order_by(ReportStep.step_code)
        .all()
    )
    return {"report_id": report.id, "steps": steps}


@router.get("/complaint/{reference_number}/steps/summary")
def list_steps_summary(reference_number: str, db: Session = Depends(get_db)):
    # First resolve reference_number → complaint → report
    complaint = (
        db.query(Complaint)
        .filter(Complaint.reference_number == reference_number)
        .first()
    )
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    report = db.query(Report).filter(Report.complaint_id == complaint.id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found")

    steps = (
        db.query(ReportStep.id, ReportStep.step_code, ReportStep.status)
        .filter(ReportStep.report_id == report.id)
        .order_by(ReportStep.step_code)
        .all()
    )
    return {
        "complaint_status": complaint.status,
        "steps": [
            {"id": s.id, "step_code": s.step_code, "status": s.status} for s in steps
        ],
    }


# ── Wildcard LAST ─────────────────────────────────────────────────────────────
@router.get("/{step_id}")
def get_step(
    step_id: int,
    db: Session = Depends(get_db),
):
    step = StepService.get_step_by_id(db, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    return step
