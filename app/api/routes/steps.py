# app/api/routes/steps.py
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from typing import Dict, Any

from app.api.deps import get_db
from app.services.step_service import StepService
from app.schemas.step_data import *
from app.models.report import Report
from app.models.report_step import ReportStep

router = APIRouter()

@router.get("/{step_id}")
def get_step(
    step_id: int,
    db: Session = Depends(get_db)
):
    """Récupère une étape par ID"""
    step = StepService.get_step_by_id(db, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    return step

@router.patch("/{step_id}/save")
def save_step_progress(
    step_id: int,
    data: Dict[Any, Any] = Body(...),
    db: Session = Depends(get_db),
):
    """
    Sauvegarde la progression d'une étape (mode brouillon)
    
    Exemple pour D5:
```json
    {
      "corrective_actions_occurrence": [
        {
          "action": "Revoir le processus de contrôle qualité",
          "responsible": "Jean Dupont",
          "due_date": "2025-03-01",
          "implementation_date": null,
          "evidence": "PV-2025-001"
        }
      ],
      "corrective_actions_detection": []
    }
```
    """
    return StepService.save_step_progress(
        db=db,
        step_id=step_id,
        data=data,
        validate_schema=True
    )

@router.post("/{step_id}/submit")
def submit_step_for_validation(
    step_id: int,
    db: Session = Depends(get_db),
):
    """
    Soumet une étape pour validation AI
    Change le statut de 'draft' à 'submitted'
    Déclenche automatiquement la validation GPT
    """
    result = StepService.submit_step(
        db=db,
        step_id=step_id,
    )
    
    return {
        "success": True,
        "step_id": result["step"].id,
        "status": result["step"].status,
        "validation": result["validation"],
        "message": result["message"]
    }

@router.get("/complaint/{complaint_id}/step/{step_code}")
def get_step_by_complaint_and_code(
    complaint_id: int,
    step_code: str,
    db: Session = Depends(get_db)
):
    """Récupère une étape par complaint_id et step_code"""
    if step_code not in ['D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8']:
        raise HTTPException(status_code=400, detail="Invalid step code")
    
    from app.models.report import Report
    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")
    
    step = StepService.get_step_by_code(db, report.id, step_code)
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    
    return step

@router.get("/complaint/{complaint_id}/steps")
def list_steps_by_complaint(complaint_id: int, db: Session = Depends(get_db)):


    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")

    steps = db.query(ReportStep).filter(ReportStep.report_id == report.id).order_by(ReportStep.step_code).all()
    return {"report_id": report.id, "steps": steps}


@router.get("/steps/{step_id}/validation")
def get_step_validation_feedback(
    step_id: int,
    db: Session = Depends(get_db)
):
    """
    Get validation feedback for a step
    """
    validation = StepService.get_step_validation(db, step_id)
    
    if not validation:
        raise HTTPException(status_code=404, detail="No validation found for this step")
    
    return {
        "decision": validation.decision,
        "missing": validation.missing,
        "issues": validation.issues,
        "suggestions": validation.suggestions,
        "professional_rewrite": validation.professional_rewrite,
        "notes": validation.notes,
        "validated_at": validation.validated_at
    }