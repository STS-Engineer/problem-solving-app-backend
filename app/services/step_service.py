from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.models.step_validation import StepValidation
from app.schemas.step_data import (
    D1Data, D2Data, D3Data, D4Data, D5Data, D6Data, D7Data, D8Data
)

STEP_SCHEMAS = {
    'D1': D1Data,
    'D2': D2Data,
    'D3': D3Data,
    'D4': D4Data,
    'D5': D5Data,
    'D6': D6Data,
    'D7': D7Data,
    'D8': D8Data,
}


class StepService:
    
    @staticmethod
    def get_step_by_id(db: Session, step_id: int) -> Optional[ReportStep]:
        """Get a step by its ID"""
        return db.query(ReportStep).filter(ReportStep.id == step_id).first()
    
    @staticmethod
    def get_step_by_code(
        db: Session, 
        report_id: int, 
        step_code: str
    ) -> Optional[ReportStep]:
        """Get a step by its code (D1-D8) for a given report"""
        return db.query(ReportStep).filter(
            ReportStep.report_id == report_id,
            ReportStep.step_code == step_code
        ).first()
    
    @staticmethod
    def save_step_progress(
        db: Session,
        step_id: int,
        data: Dict[Any, Any],
        validate_schema: bool = True
    ) -> ReportStep:
        """
        Save step progress (draft mode)
        
        Args:
            db: Database session
            step_id: Step ID
            data: JSON data to save
            validate_schema: If True, validate Pydantic schema before saving
        """
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        
        # Schema validation if requested
        if validate_schema:
            schema_class = STEP_SCHEMAS.get(step.step_code)
            if schema_class:
                try:
                    # Validate and normalize data
                    validated_data = schema_class(**data)
                    data = validated_data.model_dump(mode="json")
                except Exception as e:
                    raise HTTPException(
                        status_code=422, 
                        detail=f"Invalid data format for {step.step_code}: {str(e)}"
                    )
        
        # Merge data (allows incremental save)
        if step.data is None:
            step.data = {}
        merged = {**step.data, **data}
        step.data = jsonable_encoder(merged)
        step.updated_at = datetime.now(timezone.utc)
        
        db.commit()
        db.refresh(step)
        return step
    
    @staticmethod
    def submit_step(
        db: Session,
        step_id: int,
    ) -> ReportStep:
        """
        Submit a step for AI validation
        Change status from 'draft' to 'submitted'
        """
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        
        if step.status not in ['draft', 'rejected']:
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot submit step with status '{step.status}'"
            )
        
        # Validate required fields
        if not StepService._validate_required_fields(step.step_code, step.data):
            raise HTTPException(
                status_code=422, 
                detail="Incomplete step data. Please fill all required fields."
            )
        #get report of step
        report = db.query(Report).filter(Report.id == step.report_id).first()
        complaint=db.query(Complaint).filter(Complaint.id==report.complaint_id).first()
        if complaint:
            complaint.status = step.step_code

        step.status = 'validated'  # Auto-validate for now (TODO: AI validation)
        step.completed_by = None
        
        step.completed_at = datetime.now(timezone.utc)
        
        db.commit()
        db.refresh(step)
        
        # TODO: Trigger AI validation here
        # AIValidationService.validate_step(step_id)
        
        return step
    
    # app/services/step_service.py (MISE À JOUR de _validate_required_fields)

    @staticmethod
    def _validate_required_fields(step_code: str, data: dict) -> bool:
            """Validate that required fields are present"""
            required_fields = {
                'D1': ['team_members'],
                'D2': ['four_w_2h'],
                'D3': ['defected_part_status'],
                'D4': ['root_cause_occurrence'],
                'D5': ['corrective_actions_occurrence'],
                'D6': ['monitoring', 'checklist'],
                'D7': ['ll_conclusion'],
                'D8': ['closure_statement']
            }

            # must be a dict with something in it
            if not isinstance(data, dict) or not data:
                return False

            # D1 specific validation
            if step_code == "D1":
                members = data.get("team_members")
                if not isinstance(members, list) or len(members) < 2:
                    return False
                required_member_fields = ["name", "function", "department", "role"]
                if not all(all(m.get(k) for k in required_member_fields) for m in members):
                    return False
                return True

            # D2 specific validation
            if step_code == "D2":
                four_w_2h = data.get("four_w_2h")
                if not isinstance(four_w_2h, dict):
                    return False
                filled_count = sum(1 for v in four_w_2h.values() if v not in (None, "", [], {}))
                return filled_count >= 3

            # D3 specific validation
            if step_code == "D3":
                defected = data.get("defected_part_status")
                if not isinstance(defected, dict):
                    return False
                checkbox_keys = ["returned", "isolated", "identified"]
                has_checkbox = any(bool(defected.get(k)) for k in checkbox_keys)
                if not has_checkbox:
                    return False
                if defected.get("isolated") and not defected.get("isolated_location", "").strip():
                    return False
                if defected.get("identified") and not defected.get("identified_method", "").strip():
                    return False
                return True

            # D4 specific validation
            if step_code == "D4":
                occ = data.get("root_cause_occurrence") or {}
                nond = data.get("root_cause_non_detection") or {}
                if not occ.get("root_cause", "").strip():
                    return False
                if not occ.get("validation_method", "").strip():
                    return False
                if not nond.get("root_cause", "").strip():
                    return False
                if not nond.get("validation_method", "").strip():
                    return False
                return True

            # D5 specific validation
            if step_code == "D5":
                occ = data.get("corrective_actions_occurrence")
                det = data.get("corrective_actions_detection")
                if not isinstance(occ, list) or not isinstance(det, list):
                    return False

                def row_ok(r: dict) -> bool:
                    if not isinstance(r, dict):
                        return False
                    if not str(r.get("action", "")).strip():
                        return False
                    if not str(r.get("responsible", "")).strip():
                        return False
                    if not str(r.get("due_date", "")).strip():
                        return False
                    return True

                has_occ = any(row_ok(r) for r in occ)
                has_det = any(row_ok(r) for r in det)
                return has_occ or has_det

            # D6 specific validation
            if step_code == "D6":
                monitoring = data.get("monitoring")
                checklist = data.get("checklist")
                
                # Require monitoring section to exist
                if not isinstance(monitoring, dict):
                    return False
                
                # At least ONE of these monitoring fields must be filled
                has_monitoring = bool(
                    (monitoring.get("monitoring_interval") or "").strip() or
                    monitoring.get("pieces_produced") or
                    monitoring.get("rejection_rate") or
                    (monitoring.get("audited_by") or "").strip() or
                    (monitoring.get("audit_date") or "").strip()
                )
                
                if not has_monitoring:
                    return False
                
                # Require checklist to exist and have items
                if not isinstance(checklist, list) or len(checklist) == 0:
                    return False
                
                # Count how many questions have at least one shift checked
                verified_count = sum(
                    1 for item in checklist 
                    if isinstance(item, dict) and (
                        item.get("shift_1") or item.get("shift_2") or item.get("shift_3")
                    )
                )
                
                completion_rate = verified_count / len(checklist) if len(checklist) > 0 else 0
                
                # At least 50% must be verified across any shift
                return completion_rate >= 0.5
            if step_code == "D7":
                # Require at least ll_conclusion OR one completed section
                ll_conclusion = data.get("ll_conclusion", "").strip()
                
                def list_has_content(lst):
                    if not isinstance(lst, list):
                        return False
                    return any(
                        any(str(v).strip() for v in item.values() if v is not None)
                        for item in lst
                        if isinstance(item, dict)
                    )
                
                has_risks = list_has_content(data.get("recurrence_risks", []))
                has_disseminations = list_has_content(data.get("lesson_disseminations", []))
                has_replications = list_has_content(data.get("replication_validations", []))
                has_kb = list_has_content(data.get("knowledge_base_updates", []))
                has_monitoring = list_has_content(data.get("long_term_monitoring", []))
                
                # Either conclusion OR at least one section filled
                return ll_conclusion or any([
                    has_risks, has_disseminations, has_replications, has_kb, has_monitoring
                ])

            # ✅ D8 specific validation
            if step_code == "D8":
                closure = data.get("closure_statement", "").strip()
                if not closure:
                    return False
                
                # Optional: also check signatures
                signatures = data.get("signatures")
                if signatures:
                    if not signatures.get("closed_by", "").strip():
                        return False
                
                return True

            # Generic fallback
            step_required = required_fields.get(step_code, [])
            if not step_required:
                return True

            def is_filled(value) -> bool:
                if value is None:
                    return False
                if isinstance(value, str):
                    return value.strip() != ""
                if isinstance(value, (list, dict)):
                    return len(value) > 0
                if isinstance(value, bool):
                    return True
                return bool(value)

            return all(is_filled(data.get(field)) for field in step_required)
    
    @staticmethod
    def reject_step(
        db: Session,
        step_id: int,
        reason: str
    ) -> ReportStep:
        """
        Reject a step and reset to draft
        Used by AI or reviewer
        """
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        
        step.status = 'rejected'
        step.updated_at = datetime.now(timezone.utc)
        
        # Create validation entry with rejection
        validation = StepValidation(
            report_step_id=step_id,
            decision='fail',
            issues=[reason],
            validated_at=datetime.now(timezone.utc)
        )
        db.add(validation)
        
        db.commit()
        db.refresh(step)
        return step
    
    @staticmethod
    def approve_step(
        db: Session,
        step_id: int,
        validation_data: dict
    ) -> ReportStep:
        """
        Approve a step
        Used after positive AI validation
        """
        step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
        if not step:
            raise HTTPException(status_code=404, detail="Step not found")
        
        step.status = 'validated'
        
        # Create validation entry
        validation = StepValidation(
            report_step_id=step_id,
            decision='pass',
            missing=validation_data.get('missing'),
            issues=validation_data.get('issues'),
            suggestions=validation_data.get('suggestions'),
            professional_rewrite=validation_data.get('professional_rewrite'),
            notes=validation_data.get('notes')
        )
        db.add(validation)
        
        db.commit()
        db.refresh(step)
        return step
    
    @staticmethod
    def get_next_step(db: Session, report_id: int) -> Optional[ReportStep]:
        """Get next incomplete step"""
        return db.query(ReportStep).filter(
            ReportStep.report_id == report_id,
            ReportStep.status.in_(['draft', 'rejected'])
        ).order_by(ReportStep.step_code).first()
    
    @staticmethod
    def list_steps(db: Session, report_id: int) -> List[ReportStep]:
        """List all steps of a report"""
        return db.query(ReportStep).filter(
            ReportStep.report_id == report_id
        ).order_by(ReportStep.step_code).all()