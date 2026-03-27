from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.complaint import (
    ComplaintCreate,
    ComplaintListItem,
    ComplaintRead,
    ComplaintUpdate,
)
from app.services.complaint_service import ComplaintService

router = APIRouter()


@router.post("", response_model=ComplaintRead, status_code=status.HTTP_201_CREATED)
def create_complaint(
    payload: ComplaintCreate,
    db: Session = Depends(get_db),
):
    """
    Créer une nouvelle plainte

    """

    complaint = ComplaintService.create_complaint(
        db=db,
        payload=payload,
    )

    return complaint


@router.get("", response_model=List[ComplaintListItem])
def list_complaints(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status_filter: Optional[str] = Query(None, alias="status"),
    product_line: Optional[str] = None,
    cqt_email: Optional[str] = Query(
        None, description="Filter by CQT email (partial, case-insensitive)"
    ),
) -> List[ComplaintListItem]:
    """List complaints with optional filters."""
    complaints = ComplaintService.list_complaints(
        db=db,
        skip=skip,
        limit=limit,
        status=status_filter,
        product_line=product_line,
        cqt_email=cqt_email,
    )
    return complaints


@router.get("/{complaint_id}", response_model=ComplaintRead)
def get_complaint(complaint_id: int, db: Session = Depends(get_db)) -> ComplaintRead:
    """Get a specific complaint by ID."""
    complaint = ComplaintService.get_complaint_by_id(db, complaint_id)
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return complaint


@router.get("/ref/{reference_number}")
def get_complaint_by_ref(
    reference_number: str,
    db: Session = Depends(get_db),
):
    complaint = ComplaintService.get_complaint_by_reference(db, reference_number)

    return complaint


@router.put("/{complaint_id}", response_model=ComplaintRead)
def update_complaint(
    complaint_id: int,
    payload: ComplaintUpdate,
    db: Session = Depends(get_db),
) -> ComplaintRead:
    """Update a complaint."""
    complaint = ComplaintService.update_complaint(db, complaint_id, payload)
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
    return complaint


@router.delete("/{complaint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_complaint(complaint_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a complaint."""
    success = ComplaintService.delete_complaint(db, complaint_id)
    if not success:
        raise HTTPException(status_code=404, detail="Complaint not found")

class CancelComplaintRequest(BaseModel):
    cqt_email: str
    reason:str
class ComplaintResponse(BaseModel):
    # ... existing fields ...
    status: str
    closed_at: datetime | None = None

@router.post(
    "/{reference_number}/cancel",
    response_model=ComplaintResponse,
    summary="Cancel a complaint (CQT email confirmation required)",
)
def cancel_complaint(
    reference_number: str,
    payload: CancelComplaintRequest,
    db: Session = Depends(get_db),
):
    return  ComplaintService.cancel_complaint(
        db=db,
        reference_number=reference_number,
        cqt_email_input=payload.cqt_email,
        reason=payload.reason
    )