# app/api/routes/reports.py

import io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.complaint import Complaint
from app.models.report import Report
from app.services.report_export_service import ReportExportService
from app.schemas.report import *

router = APIRouter()

@router.get("/complaint/{reference_number}/export")
async def export_by_complaint(
    reference_number: str,         
    db: Session = Depends(get_db),
):
    _, report = _get_report_by_ref(reference_number, db)

    if not report:
        raise HTTPException(
            status_code=404,
            detail="No 8D report found for this complaint"
        )

    if not ReportExportService.is_report_ready(report):
        raise HTTPException(
            status_code=409,
            detail="8D report is not ready for export yet."
        )

    if report.report_url:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=report.report_url)

    try:
        url = await ReportExportService.save_to_github(db, report.id)
        report.report_url = url
        db.commit()
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=url)
    except Exception:
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

@router.get("/complaint/{reference_number}/preview")
def preview_by_complaint(
    reference_number: str,
    db: Session = Depends(get_db),
):
    complaint, report = _get_report_by_ref(reference_number, db)

    if not report:
        return {
            "report_number": None,
            "title": complaint.complaint_name,
            "complaint_status": complaint.status,
            "steps": {},
        }

    steps_data = {}
    for step in report.steps:
        files_by_scope: dict = {}
        for sf in step.step_files:
            key = f"{sf.action_type}:{sf.action_index}" if sf.action_type and sf.action_index is not None else "global"
            files_by_scope.setdefault(key, []).append({
                "id": sf.id,                           
                "file_id": sf.file_id,
                "filename": sf.file.original_name,     
                "mime_type": sf.file.mime_type,
                "size_bytes": sf.file.size_bytes,
                "action_type": sf.action_type,
                "action_index": sf.action_index,
                "uploaded_at": sf.created_at.isoformat() if sf.created_at else None,
            })

        steps_data[step.step_code] = {
            "status": step.status,
            "data": step.data or {},
            "files": files_by_scope,
            "step_db_id": step.id,                   
        }

    return {
        "report_number": report.report_number,
        "title": report.title,
        "complaint_status": complaint.status,
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



def _get_report_by_ref(reference_number: str, db: Session):
    complaint = (
        db.query(Complaint)
        .filter(Complaint.reference_number == reference_number)
        .first()
    )
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    report = db.query(Report).filter(Report.complaint_id == complaint.id).first()
    return complaint, report  # report may be None