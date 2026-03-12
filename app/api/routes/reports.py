# app/api/routes/reports.py

import io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.report_export_service import ReportExportService
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
async def export_by_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
):
    from app.models.report import Report

    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")

    if not ReportExportService.is_report_ready(report):
        raise HTTPException(status_code=409, detail="8D report is not ready for export yet.")

    # If already saved to GitHub, redirect to it
    if report.report_url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=report.report_url)

    # First time: generate, save to GitHub, persist URL
    try:
        url = await ReportExportService.save_to_github(db, report.id)
        report.report_url = url
        db.commit()
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=url)
    except Exception:
        # Fallback: stream directly without saving
        return _stream_excel(db, report.id)

# ── 3. Export by report_id  (direct / admin use) ──────────────────────────────

@router.get("/{report_id}/export")
def export_by_report_id(
    report_id: int,
    db: Session = Depends(get_db),
):
    from app.models.report import Report

    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if not ReportExportService.is_report_ready(report):
        raise HTTPException(
            status_code=409,
            detail="8D report is not ready for export yet. Please complete D1 to D8 first.",
        )

    return _stream_excel(db, report.id)
# ── shared helper ─────────────────────────────────────────────────────────────

@router.get("/complaint/{complaint_id}/preview")
def preview_by_complaint(
    complaint_id: int,
    db: Session = Depends(get_db),
):
    from app.models.report import Report

    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No report found")

    steps_data = {}
    for step in report.steps:
        steps_data[step.step_code] = {
            "status": step.status,
            "data": step.data or {},
        }

    return {
        "report_number": report.report_number,
        "title": report.title,
        "steps": steps_data,
    }


def _stream_excel(db: Session, report_id: int) -> StreamingResponse:
    from app.models.report import Report
    from datetime import datetime, timezone

    report = db.query(Report).filter(Report.id == report_id).first()

    # ── Auto-close complaint on first export ──────────────────────────────
    if report and report.complaint:
        complaint = report.complaint
        if (complaint.status or "").lower() not in ("closed", "resolved"):
            complaint.status = "closed"
            complaint.closed_at = datetime.now(timezone.utc)
            db.commit()

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