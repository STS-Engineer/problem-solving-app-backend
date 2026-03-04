import io
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.api.deps import get_db
# from app.services.report_export_service import ReportExportService
from app.services.step_service import StepService
from app.schemas.report import *

router = APIRouter()



@router.get("/complaint/{complaint_id}/current-step")
def get_current_step_by_complaint(
    complaint_id: int,
    db: Session = Depends(get_db)
):
    """
    Récupère l'étape courante d'un rapport par complaint_id
    Retourne l'étape en cours ou la prochaine étape à compléter
    """
    from app.models.report import Report
    
    # Trouver le rapport lié à cette plainte
    report = db.query(Report).filter(Report.complaint_id == complaint_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="No 8D report found for this complaint")
    
    # Récupérer la prochaine étape non complétée
    current_step = StepService.get_next_step(db, report.id)
    
    if not current_step:
        # Si toutes les étapes sont complétées, retourner D8
        from app.models.report_step import ReportStep
        d8_step = db.query(ReportStep).filter(
            ReportStep.report_id == report.id,
            ReportStep.step_code == 'D8'
        ).first()
        
        return {
            "report_id": report.id,
            "current_step_code": "D8",
            "step_id": d8_step.id if d8_step else None,
            "all_completed": True
        }
    
    return {
        "report_id": report.id,
        "current_step_code": current_step.step_code,
        "step_id": current_step.id,
        "all_completed": False
    }


# @router.get("/{report_id}/export")
# def export_report(
#     report_id: int,
#     db: Session = Depends(get_db),
# ):
#     """
#     Generate and download the complete filled 8D Excel report.
#     All step data (D1-D8) is pulled from the DB and written into the template.
#     """
#     try:
#         file_bytes = ReportExportService.generate_excel(db, report_id)
#         filename   = ReportExportService.get_filename(db, report_id)
#     except ValueError as e:
#         raise HTTPException(status_code=404, detail=str(e))
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

#     return StreamingResponse(
#         io.BytesIO(file_bytes),
#         media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#         headers={"Content-Disposition": f'attachment; filename="{filename}"'},
#     )