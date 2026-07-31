from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.complaint import (
    ArchivedComplaintFile,
    ArchivedComplaintListItem,
    ComplaintCreate,
    ComplaintListItem,
    ComplaintRead,
    ComplaintUpdate,
)
from app.services.complaint_service import ComplaintService
from app.services.file_storage import storage

router = APIRouter()


@router.get("/archived", response_model=dict)
def list_archived_complaints(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(
        None, description="Search complaint name, customer, or reference number"
    ),
    customer: Optional[str] = Query(None, description="Filter by exact customer"),
    plant: Optional[str] = Query(None, description="Filter by exact AvoCarbon plant"),
):
    """
    Historical complaints archived from the Monday.com "Close" board —
    no 8D step data, just the stored metadata and whatever files were
    attached on Monday. Registered before /{complaint_id} deliberately so
    "archived" is never mistaken for a numeric id.
    """
    complaints = ComplaintService.list_archived_complaints(
        db=db, skip=skip, limit=limit, search=search, customer=customer, plant=plant
    )
    total = ComplaintService.count_archived_complaints(
        db=db, search=search, customer=customer, plant=plant
    )

    items = []
    for c in complaints:
        files = [
            ArchivedComplaintFile(
                id=f.id,
                original_name=f.original_name,
                url=storage.url_for(f.stored_path),
                size_bytes=f.size_bytes,
                mime_type=f.mime_type,
            )
            for f in getattr(c, "_archived_files", [])
        ]
        items.append(
            ArchivedComplaintListItem(
                id=c.id,
                reference_number=c.reference_number,
                complaint_name=c.complaint_name,
                customer=c.customer,
                customer_plant_name=c.customer_plant_name,
                avocarbon_plant=c.avocarbon_plant.value if c.avocarbon_plant else None,
                product_line=c.product_line.value if c.product_line else None,
                quality_issue_warranty=c.quality_issue_warranty,
                defects=c.defects,
                concerned_application=c.concerned_application,
                avocarbon_product_type=c.avocarbon_product_type,
                potential_avocarbon_process_linked_to_problem=c.potential_avocarbon_process_linked_to_problem,
                complaint_description=c.complaint_description,
                customer_complaint_date=c.customer_complaint_date,
                complaint_opening_date=c.complaint_opening_date,
                closed_at=c.closed_at,
                repetitive_complete_with_number=c.repetitive_complete_with_number,
                external_reference=c.external_reference,
                files=files,
            )
        )

    return {"items": items, "total": total, "skip": skip, "limit": limit}


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