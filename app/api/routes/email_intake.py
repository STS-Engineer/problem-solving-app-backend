"""
app/api/routes/email_intake.py

Lenient intake endpoint the ChatGPT/MCP agent calls once per email, plus
read-only listing for the review UI. The strict complaint validation is NOT
run here — see EmailIntakeService and the (future) promote endpoint.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.models.email_intake import EmailIntake
from app.schemas.email_intake import (
    EmailIntakeAssign,
    EmailIntakeCreate,
    EmailIntakeListItem,
    EmailIntakeRead,
    EmailIntakeResult,
    EmailIntakeSetPlant,
)
from app.services.email_intake_service import EmailIntakeService

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_intake_key(x_intake_key: Optional[str] = Header(default=None)) -> None:
    """
    Verify the shared secret sent by the agent. If INTAKE_API_KEY is unset the
    check is skipped (dev only) with a loud warning.
    """
    expected = settings.INTAKE_API_KEY.strip()
    if not expected:
        logger.warning("INTAKE_API_KEY is not set — /intake/email is UNAUTHENTICATED")
        return
    if x_intake_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Intake-Key")


@router.post(
    "/email",
    response_model=EmailIntakeResult,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a complaint email (agent → staging), idempotent",
)
def ingest_email(
    payload: EmailIntakeCreate,
    db: Session = Depends(get_db),
    _: None = Depends(_require_intake_key),
) -> EmailIntakeResult:
    intake, result = EmailIntakeService.ingest(db, payload)
    return EmailIntakeResult(
        status=result,
        intake_id=intake.id,
        notified_to=list(intake.notified_to or []),
    )


@router.get("", response_model=List[EmailIntakeListItem], summary="List intakes")
def list_intakes(
    db: Session = Depends(get_db),
    status_filter: Optional[str] = Query("pending_review", alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> List[EmailIntakeListItem]:
    q = db.query(EmailIntake)
    if status_filter:
        q = q.filter(EmailIntake.status == status_filter)
    return (
        q.order_by(EmailIntake.created_at.desc()).offset(skip).limit(limit).all()
    )


@router.get("/{intake_id}", response_model=EmailIntakeRead, summary="Get one intake")
def get_intake(intake_id: int, db: Session = Depends(get_db)) -> EmailIntakeRead:
    intake = db.get(EmailIntake, intake_id)
    if not intake:
        raise HTTPException(status_code=404, detail="Intake not found")
    return intake


@router.patch(
    "/{intake_id}/plant",
    response_model=EmailIntakeRead,
    summary="Triage: set/correct the responsible plant (optionally re-notify QM/PM)",
)
def set_plant(
    intake_id: int,
    payload: EmailIntakeSetPlant,
    db: Session = Depends(get_db),
) -> EmailIntakeRead:
    try:
        intake = EmailIntakeService.set_plant(
            db, intake_id=intake_id, plant=payload.plant, renotify=payload.renotify
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return intake


@router.post(
    "/{intake_id}/assign",
    response_model=EmailIntakeRead,
    summary="QM assigns a CQT to the intake and notifies them",
)
def assign_cqe(
    intake_id: int,
    payload: EmailIntakeAssign,
    db: Session = Depends(get_db),
) -> EmailIntakeRead:
    try:
        intake = EmailIntakeService.assign_cqe(
            db,
            intake_id=intake_id,
            cqe_email=payload.cqe_email,
            assigned_by=payload.assigned_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return intake
