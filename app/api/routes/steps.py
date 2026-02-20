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
from app.services.section_config import STEP_SECTIONS

router = APIRouter()



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

@router.get("/complaint/{complaint_id}/steps/summary")
def list_steps_summary(complaint_id: int, db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found")

    steps = (
        db.query(ReportStep.id, ReportStep.step_code, ReportStep.status)
        .filter(ReportStep.report_id == report.id)
        .order_by(ReportStep.step_code)
        .all()
    )
    return [{"id": s.id, "step_code": s.step_code, "status": s.status} for s in steps]


@router.patch("/{step_id}/save")
def save_step_progress(
    step_id: int,
    data: Dict[Any, Any] = Body(...),
    db: Session = Depends(get_db),
):
    return StepService.save_step_progress(db=db, step_id=step_id, data=data, validate_schema=True)


# ─────────────────────────────────────────────────────────────────────────────
# PER-SECTION VALIDATION  ← NEW
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{step_id}/submit-section")
def submit_section_for_validation(
    step_id: int,
    payload: Dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """
    Validate a single named section of a step via the AI coach.

    Body: { "section_key": "five_w_2h" }

    Returns:
    {
      "success": true,
      "step_id": int,
      "section_key": str,
      "validation": ValidationResult,
      "all_sections_passed": bool,
      "passed_sections": [str],
      "remaining_sections": [str]
    }
    """
    section_key = payload.get("section_key")
    if not section_key:
        raise HTTPException(status_code=400, detail="section_key is required in request body")

    result = StepService.submit_section(db=db, step_id=step_id, section_key=section_key)

    return {
        "success": True,
        "step_id": step_id,
        "section_key": section_key,
        "validation": result["validation"],
        "all_sections_passed": result["all_sections_passed"],
        "passed_sections": result["passed_sections"],
        "remaining_sections": result["remaining_sections"],
    }


@router.get("/{step_id}/section-validations")
def get_all_section_validations(
    step_id: int,
    db: Session = Depends(get_db),
):
    """
    Return all saved section validations for a step.
    Useful for restoring UI state (which sections are already passed).

    Returns:
    {
      "step_id": int,
      "sections": {
        "five_w_2h": { decision, missing_fields, quality_issues, ... },
        "deviation":  { ... },
        ...
      }
    }
    """
    rows = StepService.get_all_section_validations(db, step_id)
    sections = {}
    for row in rows:
        field_improvements = {}
        if row.professional_rewrite:
            try:
                field_improvements = json.loads(row.professional_rewrite)
            except (json.JSONDecodeError, TypeError):
                pass

        sections[row.section_key] = {
            "decision":          row.decision,
            "missing_fields":    row.missing or [],
            "quality_issues":    row.issues or [],
            "suggestions":       row.suggestions or [],
            "field_improvements": field_improvements,
            "overall_assessment": row.notes or "",
            "validated_at":      row.validated_at.isoformat() if row.validated_at else None,
        }

    return {"step_id": step_id, "sections": sections}


# ─────────────────────────────────────────────────────────────────────────────
# FULL STEP SUBMIT  (D1 + legacy)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/{step_id}/submit")
def submit_step_for_validation(
    step_id: int,
    db: Session = Depends(get_db),
):
    """
    Full-step validation. Used for D1 (local validation).
    D2-D8 should use /submit-section instead.
    """
    result = StepService.submit_step(db=db, step_id=step_id)
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
    validation = StepService.get_step_validation(db, step_id)
    if not validation:
        raise HTTPException(status_code=404, detail="No validation found for this step")

    field_improvements: dict = {}
    if validation.professional_rewrite:
        try:
            field_improvements = json.loads(validation.professional_rewrite)
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "decision":           validation.decision,
        "missing_fields":     validation.missing or [],
        "incomplete_fields":  [],
        "quality_issues":     validation.issues or [],
        "rules_violations":   [],
        "suggestions":        validation.suggestions or [],
        "field_improvements": field_improvements,
        "overall_assessment": validation.notes or "",
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