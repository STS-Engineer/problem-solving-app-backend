from typing import List, Optional
import random
import string
from sqlalchemy.orm import Session

from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.schemas.complaint import ComplaintCreate, ComplaintUpdate
from app.services.utils.report_helpers import generate_report_number, get_8d_steps_definitions

def generate_complaint_number():
    return "C-" + "".join(random.choices(string.digits, k=5))


class ComplaintService:
    @staticmethod
    def create_complaint(
        db: Session, 
        payload: ComplaintCreate,
    ) -> Complaint:
        """
        Crée une plainte et optionnellement initialise un rapport 8D
        
        Args:
            db: Session de base de données
            payload: Données de la plainte
            create_report: Si True, crée automatiquement un rapport 8D
            report_title: Titre du rapport (optionnel, sinon utilise le nom de la plainte)
        """
        # 1. Créer la plainte
        complaint = Complaint(**payload.model_dump())
        complaint.reference_number = generate_complaint_number()
        db.add(complaint)
        db.flush()  # Pour obtenir l'ID de la plainte
        
        # 2. Optionnellement créer le rapport 8D
        report = Report(
                complaint_id=complaint.id,
                report_number=generate_report_number(),
                title=f"8D Report - {complaint.complaint_name}",
                plant=complaint.avocarbon_plant,
                created_by=complaint.reported_by,
                status='draft'
            )
        db.add(report)
        db.flush()  # Pour obtenir l'ID du rapport
            
            # 3. Initialiser les 8 étapes
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
        db.refresh(complaint)
        return complaint
    
    @staticmethod
    def get_complaint_by_id(db: Session, complaint_id: int) -> Optional[Complaint]:
        """Récupère une plainte par son ID"""
        return db.query(Complaint).filter(Complaint.id == complaint_id).first()
    
    @staticmethod
    def get_complaint_by_reference(db: Session, reference_number: str) -> Optional[Complaint]:
        """Récupère une plainte par son numéro de référence"""
        return db.query(Complaint).filter(
            Complaint.reference_number == reference_number
        ).first()
    
    @staticmethod
    def list_complaints(
        db: Session,
        skip: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        product_line: Optional[str] = None,
    ) -> List[Complaint]:
        query = db.query(Complaint)
        
        if status:
            query = query.filter(Complaint.status == status)
        if product_line:
            query = query.filter(Complaint.product_line == product_line)
        
        return query.order_by(Complaint.created_at.desc()).offset(skip).limit(limit).all()

    @staticmethod
    def update_complaint(
        db: Session, complaint_id: int, payload: ComplaintUpdate
    ) -> Optional[Complaint]:
        complaint = ComplaintService.get_complaint_by_id(db, complaint_id)
        if not complaint:
            return None

        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(complaint, key, value)

        db.add(complaint)
        db.commit()
        db.refresh(complaint)
        return complaint

    @staticmethod
    def delete_complaint(db: Session, complaint_id: int) -> bool:
        complaint = ComplaintService.get_complaint_by_id(db, complaint_id)
        if not complaint:
            return False

        db.delete(complaint)
        db.commit()
        return True