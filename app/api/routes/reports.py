# app/api/routes/reports.py

import io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.report_export_service import ReportExportService
from app.services.step_service import StepService
from app.schemas.report import *

router = APIRouter()

# Ordered 8D step codes
_STEP_ORDER = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]


# ── 1. Current step ───────────────────────────────────────────────────────────

@router.get("/complaint/{complaint_id}/current-step")
def get_current_step_by_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
):
    from app.models.complaint import Complaint
    from app.models.report import Report
    from app.models.report_step import ReportStep

    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()

    # ── No Report row (legacy complaint) — fall back to complaint.status ──────
    if not report:
        complaint = db.query(Complaint).filter(Complaint.id == complaint_id).first()
        if not complaint:
            raise HTTPException(status_code=404, detail="Complaint not found")
        fallback = (complaint.status or "open").upper()
        return {
            "report_id":         None,
            "current_step_code": fallback,
            "step_code":         fallback,
            "step_id":           None,
            "all_completed":     False,
            "has_report":        False,
        }

    # ── Load all steps for this report ───────────────────────────────────────
    all_steps = (
        db.query(ReportStep)
        .filter(ReportStep.report_id == report.id)
        .all()
    )

    # Build a lookup: step_code → ReportStep
    step_map = {s.step_code: s for s in all_steps}

    # ── Determine current step by walking the order ───────────────────────────
    # The current step is the first one that is NOT fulfilled.
    # If all are fulfilled, the complaint is done (return D8).
    current_step = None
    for code in _STEP_ORDER:
        step = step_map.get(code)
        if step is None:
            # Step missing entirely — treat as not started
            current_step = step
            break
        if step.status != "fulfilled":
            current_step = step
            break

    if current_step is None:
        # Every step is fulfilled → all done
        d8 = step_map.get("D8")
        step_code = "D8"
        return {
            "report_id":         report.id,
            "current_step_code": step_code,
            "step_code":         step_code,
            "step_id":           d8.id if d8 else None,
            "all_completed":     True,
            "has_report":        True,
        }

    step_code = current_step.step_code
    return {
        "report_id":         report.id,
        "current_step_code": step_code,
        "step_code":         step_code,
        "step_id":           current_step.id,
        "all_completed":     False,
        "has_report":        True,
    }


# ── 2. Export by complaint_id  (called from D8 page) ─────────────────────────
# NOTE: this route MUST be registered BEFORE /{report_id}/export
# so FastAPI does not try to parse "complaint" as an integer.

@router.get("/complaint/{complaint_id}/export")
def export_by_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
):
    """
    Generate and download the filled 8D Excel report for a given complaint.
    Fetches all D1-D8 step data + photos from GitHub, writes them into the template.
    """
    from app.models.report import Report

    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")

    return _stream_excel(db, report.id)


# ── 3. Export by report_id  (direct / admin use) ──────────────────────────────

@router.get("/{report_id}/export")
def export_by_report_id(
    report_id: int,
    db: Session = Depends(get_db),
):
    """Generate and download the filled 8D Excel report for a given report_id."""
    return _stream_excel(db, report_id)


# ── shared helper ─────────────────────────────────────────────────────────────

def _stream_excel(db: Session, report_id: int) -> StreamingResponse:
    try:
        file_bytes = ReportExportService.generate_excel(db, report_id)
        filename   = ReportExportService.get_filename(db, report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}")

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )