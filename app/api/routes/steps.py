from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.exceptions import (
    ComplaintNotFoundError,
    InvalidStepCodeError,
    ReportNotFoundError,
    StepNotFoundError,
)
from app.services.step_service import StepService

router = APIRouter()


@router.get("/complaint/{reference_number}/step/{step_code}")
def get_step_by_complaint_and_code(
    reference_number: str,
    step_code: str,
    db: Session = Depends(get_db),
):
    try:
        return StepService.get_step_by_complaint_and_code(
            db=db,
            reference_number=reference_number,
            step_code=step_code,
        )
    except InvalidStepCodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ComplaintNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StepNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/complaint/{reference_number}/steps/summary")
def list_steps_summary(
    reference_number: str,
    db: Session = Depends(get_db),
):
    try:
        return StepService.get_steps_summary_by_complaint(
            db=db,
            reference_number=reference_number,
        )
    except ComplaintNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc