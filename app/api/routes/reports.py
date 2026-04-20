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
            status_code=404, detail="No 8D report found for this complaint"
        )

    if not ReportExportService.is_report_ready(report):
        raise HTTPException(
            status_code=409, detail="8D report is not ready for export yet."
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


@router.get("/complaint/{complaint_id}/d3-pdf")
@router.get("/complaint/{complaint_id}/d1-d3-pdf")
def export_d1_d3_pdf(
    complaint_id: str,
    db: Session = Depends(get_db),
):
    return _stream_partial_pdf(
        db=db,
        complaint_id=complaint_id,
        export_kind="d1_d3",
        filename_prefix="8D_D1-D3_report",
    )


@router.get("/complaint/{complaint_id}/d1-d5-pdf")
def export_d1_d5_pdf(
    complaint_id: str,
    db: Session = Depends(get_db),
):
    return _stream_partial_pdf(
        db=db,
        complaint_id=complaint_id,
        export_kind="d1_d5",
        filename_prefix="8D_D1-D5_report",
    )


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
            key = (
                f"{sf.action_type}:{sf.action_index}"
                if sf.action_type and sf.action_index is not None
                else "global"
            )
            files_by_scope.setdefault(key, []).append(
                {
                    "id": sf.id,
                    "file_id": sf.file_id,
                    "filename": sf.file.original_name,
                    "mime_type": sf.file.mime_type,
                    "size_bytes": sf.file.size_bytes,
                    "action_type": sf.action_type,
                    "action_index": sf.action_index,
                    "uploaded_at": sf.created_at.isoformat() if sf.created_at else None,
                }
            )

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
        filename = ReportExportService.get_filename(db, report_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}")

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _stream_partial_pdf(
    db: Session,
    complaint_id: str,
    export_kind: str,
    filename_prefix: str,
) -> StreamingResponse:
    _, report = _get_report_by_identifier(complaint_id, db)

    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")

    try:
        from app.services import pdf_service

        if export_kind == "d1_d3":
            file_bytes = pdf_service.generate_report_d1_d3(db, complaint_id)
        elif export_kind == "d1_d5":
            file_bytes = pdf_service.generate_report_d1_to_d5(db, complaint_id)
        else:
            raise HTTPException(status_code=500, detail=f"Unsupported PDF export kind '{export_kind}'")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"PDF dependencies are not installed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"PDF export failed: {exc}")

    filename = f"{filename_prefix}_{report.report_number}.pdf"
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _get_report_by_ref(reference_number: str, db: Session):
    return _get_report_by_identifier(reference_number, db)


def _get_report_by_identifier(complaint_identifier: str, db: Session):
    complaint = (
        db.query(Complaint)
        .filter(Complaint.reference_number == complaint_identifier)
        .first()
    )
    if not complaint and complaint_identifier.isdigit():
        complaint = db.query(Complaint).filter(Complaint.id == int(complaint_identifier)).first()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    report = db.query(Report).filter(Report.complaint_id == complaint.id).first()
    return complaint, report  # report may be None
