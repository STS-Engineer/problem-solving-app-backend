# app/api/routes/steps.py
import json
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.api.deps import get_db
from app.services.step_service import StepService
from app.schemas.step_data import *
from app.models.report import Report
from app.models.report_step import ReportStep

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTANT: specific paths MUST be registered before wildcard /{step_id}
# FastAPI matches routes in declaration order.
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/complaint/{complaint_id}/step/{step_code}")
def get_step_by_complaint_and_code(
    complaint_id: int,
    step_code: str,
    db: Session = Depends(get_db),
):
    if step_code not in ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]:
        raise HTTPException(status_code=400, detail="Invalid step code")

    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")

    step = StepService.get_step_by_code(db, report.id, step_code)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")

    return step


@router.get("/complaint/{complaint_id}/steps")
def list_steps_by_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
):
    """
    Returns ALL steps for a complaint.
    Shape: { report_id: int, steps: StepData[] }
    The frontend must extract .steps — see reports.ts getStepsByComplaintId fix.
    """
    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")

    steps = (
        db.query(ReportStep)
        .filter(ReportStep.report_id == report.id)
        .order_by(ReportStep.step_code)
        .all()
    )

    return {"report_id": report.id, "steps": steps}


@router.patch("/{step_id}/save")
def save_step_progress(
    step_id: int,
    data: Dict[Any, Any] = Body(...),
    db: Session = Depends(get_db),
):
    return StepService.save_step_progress(db=db, step_id=step_id, data=data, validate_schema=True)


@router.post("/{step_id}/submit")
def submit_step_for_validation(
    step_id: int,
    db: Session = Depends(get_db),
):
    """
    Submit a step for AI validation.
    Returns a ValidationResult-shaped object the frontend can consume directly.
    """
    result = StepService.submit_step(db=db, step_id=step_id)

    # result["validation"] already has the correct keys from ResponseParser /
    # D1LocalValidator, so we pass it through untouched.
    return {
        "success": True,
        "step_id": result["step"].id,
        "status": result["step"].status,
        "validation": result["validation"],
        "message": result["message"],
    }


@router.get("/{step_id}/validation")
def get_step_validation_feedback(
    step_id: int,
    db: Session = Depends(get_db),
):
    """
    Return validation feedback for a step.

    DB columns (StepValidation model):
        missing              ARRAY(Text)  → frontend: missing_fields
        issues               ARRAY(Text)  → frontend: quality_issues
        suggestions          ARRAY(Text)  → frontend: suggestions
        professional_rewrite Text (JSON)  → frontend: field_improvements (dict)
        notes                Text         → frontend: overall_assessment

    The frontend ValidationResult interface expects:
        { decision, missing_fields, incomplete_fields, quality_issues,
          rules_violations, suggestions, field_improvements,
          overall_assessment, language_detected }
    """
    validation = StepService.get_step_validation(db, step_id)

    if not validation:
        raise HTTPException(status_code=404, detail="No validation found for this step")

    # Parse field_improvements from the JSON string stored in professional_rewrite
    field_improvements: dict = {}
    if validation.professional_rewrite:
        try:
            field_improvements = json.loads(validation.professional_rewrite)
        except (json.JSONDecodeError, TypeError):
            field_improvements = {}

    return {
        # ── exact field names the frontend ValidationResult type expects ──
        "decision":           validation.decision,
        "missing_fields":     validation.missing or [],       # ARRAY(Text) → list
        "incomplete_fields":  [],                              # merged into issues on write
        "quality_issues":     validation.issues or [],        # ARRAY(Text) → list
        "rules_violations":   [],                              # merged into issues on write
        "suggestions":        validation.suggestions or [],   # ARRAY(Text) → list
        "field_improvements": field_improvements,             # dict
        "overall_assessment": validation.notes or "",         # str
        "language_detected":  "en",
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