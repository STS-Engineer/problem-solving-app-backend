"""
Gestion du cycle de vie complet du rapport 8D
"""
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models import Report, ReportStep, Complaint
from app.schemas.report import ReportCreate
from app.services.utils.report_helpers import generate_report_number, get_8d_steps_definitions

class ReportService:
    
    @staticmethod
    def create_report_from_complaint(
        db: Session, 
        complaint_id: int, 
        user_id: int,
        report_data: ReportCreate
    ) -> Report:
        """
        Crée un rapport 8D et initialise toutes les étapes
        """
        # 1. Créer le rapport
        report = Report(
            complaint_id=complaint_id,
            report_number=generate_report_number(),
            title=report_data.title,
            plant=report_data.plant,
            created_by=user_id,
            status='draft'
        )
        db.add(report)
        db.flush()
        
        # 2. Initialiser les 8 étapes
        steps_definitions = get_8d_steps_definitions()
        for step_def in steps_definitions:
            step = ReportStep(
                report_id=report.id,
                step_code=step_def['code'],
                step_name=step_def['name'],
                status='draft',
                data={}
            )
            db.add(step)
        
        db.commit()
        db.refresh(report)
        return report
    
    @staticmethod
    def get_report_progress(db: Session, report_id: int) -> dict:
        """
        Calcule la progression du rapport
        """
        steps = db.query(ReportStep).filter(
            ReportStep.report_id == report_id
        ).all()
        
        total = len(steps)
        completed = sum(1 for s in steps if s.status == 'validated')
        
        return {
            'total_steps': total,
            'completed_steps': completed,
            'progress_percentage': (completed / total * 100) if total > 0 else 0,
            'current_step': next((s for s in steps if s.status == 'draft'), None)
        }