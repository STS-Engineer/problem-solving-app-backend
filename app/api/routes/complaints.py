from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.complaint import ComplaintCreate, ComplaintListItem, ComplaintRead, ComplaintUpdate
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
) -> List[ComplaintListItem]:
    """List complaints with optional filters."""
    complaints = ComplaintService.list_complaints(
        db=db,
        skip=skip,
        limit=limit,
        status=status_filter,
        product_line=product_line,
    )
    return complaints


@router.get("/{complaint_id}", response_model=ComplaintRead)
def get_complaint(
    complaint_id: int, 
    db: Session = Depends(get_db)
) -> ComplaintRead:
    """Get a specific complaint by ID."""
    complaint = ComplaintService.get_complaint_by_id(db, complaint_id)
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")
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
def delete_complaint(
    complaint_id: int, 
    db: Session = Depends(get_db)
) -> None:
    """Delete a complaint."""
    success = ComplaintService.delete_complaint(db, complaint_id)
    if not success:
        raise HTTPException(status_code=404, detail="Complaint not found")