from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import logging

from app.api.deps import get_db
from app.services.chatbot_service import ChatbotService
from app.schemas.chatbot import (
    StepValidationRequest,
    StepValidationResponse,
    ValidationFeedback,
    HealthCheckResponse
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/validate-step", response_model=StepValidationResponse)
def validate_step(
    request: StepValidationRequest,
    db: Session = Depends(get_db)
):
    """Validate step with enriched AI analysis"""
    try:
        logger.info("🚀 Validating step %s (ID: %d)", 
                   request.step_code, request.report_step_id)
        
        chatbot = ChatbotService(db)
        
        validation = chatbot.validate_step(
            report_step_id=request.report_step_id,
            step_code=request.step_code,
            step_data=request.step_data
        )
        
        feedback = ValidationFeedback(
            decision=validation['decision'],
            missing_fields=validation.get('missing_fields', []),
            incomplete_fields=validation.get('incomplete_fields', []),
            quality_issues=validation.get('quality_issues', []),
            rules_violations=validation.get('rules_violations', []),  # ✨ AJOUTÉ
            suggestions=validation.get('suggestions', []),
            field_improvements=validation.get('field_improvements', {}),
            overall_assessment=validation.get('overall_assessment', ''),
            language_detected=validation.get('language_detected', 'en')  # ✨ AJOUTÉ
        )
        
        if validation['decision'] == 'pass':
            message = f"✅ Step {request.step_code} validated! Proceed to next step."
        else:
            message = f"❌ Step {request.step_code} needs improvements."
        
        return StepValidationResponse(
            success=True,
            validation=feedback,
            message=message,
            can_proceed=(validation['decision'] == 'pass'),
            report_step_id=request.report_step_id
        )
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Validation error")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=HealthCheckResponse)
def health_check(db: Session = Depends(get_db)):
    """Health check with enriched status"""
    try:
        chatbot = ChatbotService(db)
        health = chatbot.health_check()
        return HealthCheckResponse(**health)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))