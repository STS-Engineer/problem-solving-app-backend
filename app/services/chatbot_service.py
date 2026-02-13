"""
Chatbot Service - AI-Powered Step Validation
Version: 2.0 - Enriched with 20 Rules + Complaint Context
"""
import json
import re
import logging
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI, OpenAIError

from app.core.config import settings

logger = logging.getLogger(__name__)


# ============================================================
# KNOWLEDGE BASE RETRIEVER 
# ============================================================

class KnowledgeBaseRetriever:
    """Retrieves all knowledge from database"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_step_coaching_content(self, step_code: str) -> str:
        """
        Get coaching document for specific step (D1-D8)
        
        Args:
            step_code: D1, D2, ..., D8
        
        Returns:
            Coaching content text
        """
        section_hint = f"{step_code}_coaching_validation"
        
        query = text("""
            SELECT k.content
            FROM kb_chunks k
            JOIN files f ON k.file_id = f.id
            WHERE k.section_hint = :section_hint
            AND f.purpose = 'ikb'
            LIMIT 1
        """)
        
        result = self.db.execute(query, {"section_hint": section_hint}).fetchone()
        
        if not result or not result[0]:
            raise ValueError(f"No coaching content found for {step_code}")
        
        logger.info("📚 Coaching loaded for %s (%d chars)", step_code, len(result[0]))
        return result[0]
    
    def get_twenty_rules(self) -> str:
        """
        Get the "20 Rules to respect on the floor" document
        
        Returns:
            20 Rules content text
        """
        query = text("""
            SELECT k.content
            FROM kb_chunks k
            JOIN files f ON k.file_id = f.id
            WHERE k.section_hint = 'floor_rules_guidelines'
            AND f.purpose = 'ikb'
            LIMIT 1
        """)
        
        result = self.db.execute(query).fetchone()
        
        if result and result[0]:
            logger.info("📜 20 Rules loaded (%d chars)", len(result[0]))
            return result[0]
        else:
            logger.warning("⚠️ 20 Rules not found in KB")
            return ""
    
    def get_complaint_context(self, report_step_id: int) -> Dict:
        """
        Get complaint context for a report step
        
        Args:
            report_step_id: ID of the report step
        
        Returns:
            Dictionary with complaint information
        """
        query = text("""
            SELECT 
                c.complaint_name,
                c.complaint_description,
                c.product_line,
                c.avocarbon_plant,
                c.defects
            FROM complaints c
            JOIN reports r ON c.id = r.complaint_id
            JOIN report_steps rs ON r.id = rs.report_id
            WHERE rs.id = :report_step_id
        """)
        
        result = self.db.execute(query, {"report_step_id": report_step_id}).fetchone()
        
        if result:
            context = {
                "complaint_name": result[0] or "",
                "complaint_description": result[1] or "",
                "product_line": result[2] or "",
                "plant": result[3] or "",
                "defects": result[4] or ""
            }
            logger.info("📋 Complaint context loaded: %s", context['complaint_name'])
            return context
        else:
            logger.warning("⚠️ No complaint found for report_step_id %d", report_step_id)
            return {}


# ============================================================
# PROMPT BUILDER 
# ============================================================

class PromptBuilder:
    """Builds enriched validation prompts"""
    
    @staticmethod
    def format_step_data(step_data: Dict) -> str:
        """Format step data into readable text"""
        if not step_data:
            return "No data provided"
        
        return "\n".join(
            f"{key.replace('_', ' ').title()}: {value}"
            for key, value in step_data.items()
        )
    
    @staticmethod
    def format_complaint_context(complaint: Dict) -> str:
        """Format complaint context"""
        if not complaint:
            return "No complaint context available"
        
        lines = [
            "COMPLAINT CONTEXT:",
            "=" * 60,
            f"Problem: {complaint.get('complaint_name', 'N/A')}",
            f"Description: {complaint.get('complaint_description', 'N/A')}",
            f"Product Line: {complaint.get('product_line', 'N/A')}",
            f"Plant: {complaint.get('plant', 'N/A')}",
            f"Defects: {complaint.get('defects', 'N/A')}",
            ""
        ]
        return "\n".join(lines)
    
    @staticmethod
    def build_enriched_validation_prompt(
        step_code: str,
        coaching: str,
        twenty_rules: str,
        complaint: Dict,
        step_data: Dict
    ) -> str:
        """
        Build enriched validation prompt with all context
        
        This is the CORE of the chatbot - combines all knowledge sources
        """
        
        formatted_complaint = PromptBuilder.format_complaint_context(complaint)
        formatted_data = PromptBuilder.format_step_data(step_data)
        
        # Include 20 Rules section only if available
        rules_section = ""
        if twenty_rules:
            rules_section = f"""
## 20 RULES TO RESPECT ON THE FLOOR (THE LAW)
{twenty_rules}

⚠️ CRITICAL: These rules are MANDATORY. When you detect a violation:
- Ask: "Is it normal that...?"
- React ONLY to OBVIOUS deviations
- Ask SUBTLE guiding questions
- Do NOT be accusatory
{"-" * 60}
"""
        
        prompt = f"""
# QUALITY AI COACH - 8D METHODOLOGY VALIDATOR

You are an intelligent assistant specialized in industrial quality management.
Your role is to VALIDATE user responses against 8D methodology standards.

{rules_section}

## COACHING DOCUMENT FOR {step_code}
{coaching}

{"-" * 60}

{formatted_complaint}

{"-" * 60}

## USER'S RESPONSE TO VALIDATE
{formatted_data}

{"-" * 60}

## YOUR MISSION

Analyze the user's response according to:

1. **COMPLETENESS**: All required fields filled per coaching document?
2. **QUALITY**: Clear, precise, professional descriptions?
3. **QUANTIFICATION**: Measurable data (%, quantities, dates, metrics)?
4. **CONSISTENCY**: Information logically coherent with complaint context?
5. **STANDARDS**: 8D best practices respected?
6. **RULES COMPLIANCE**: Any of the 20 Rules violated? (if applicable)

## VALIDATION CRITERIA

**PASS if:**
- All essential criteria from coaching document are met
- Response is complete and detailed
- Data is quantified and specific
- Consistent with the complaint context

**FAIL if:**
- Missing critical information
- Too vague or generic
- Lacks quantification
- Inconsistent with complaint
- Obvious rule violation detected

## RESPONSE FORMAT (JSON ONLY)

Return STRICTLY this JSON structure:

{{
    "decision": "pass" or "fail",
    "missing_fields": ["field1", "field2"],
    "incomplete_fields": ["field3 needs more detail"],
    "quality_issues": ["issue1", "issue2"],
    "rules_violations": ["Rule X: explanation (ask: is it normal that...?)"],
    "suggestions": ["concrete suggestion 1", "concrete suggestion 2"],
    "field_improvements": {{
        "field_name": "improved version example"
    }},
    "overall_assessment": "Your professional assessment",
    "language_detected": "en"
}}

## COACHING STYLE

- Be DEMANDING but CONSTRUCTIVE
- Point out SPECIFIC improvements needed
- Use complaint context to make suggestions RELEVANT
- If rules violated, ask subtly: "Is it normal that...?"
- Provide CONCRETE examples in field_improvements

## LANGUAGE

- Auto-detect user's language from their response
- Respond in the SAME language
- Set "language_detected" field appropriately

Now validate the user's response according to these criteria.
"""
        
        return prompt


# ============================================================
# OPENAI CLIENT
# ============================================================

class OpenAIClient:
    """OpenAI API wrapper"""
    
    def __init__(self):
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
    
    def validate_step(self, prompt: str) -> str:
        """
        Call OpenAI to validate step
        Args:
            prompt: Complete validation prompt
        Returns:
            JSON response as string
        """
        try:
            logger.info("🤖 Calling OpenAI %s...", settings.OPENAI_MODEL)
            
            response = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert 8D quality coach. Return ONLY valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=settings.OPENAI_TEMPERATURE,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                response_format={"type": "json_object"},
                timeout=30
            )
            
            content = response.choices[0].message.content
            logger.info("✅ OpenAI response received (%d chars)", len(content))
            
            return content
            
        except OpenAIError as e:
            logger.error("❌ OpenAI API error: %s", str(e))
            raise RuntimeError("OpenAI validation failed")
# ============================================================
# RESPONSE PARSER
# ============================================================

class ResponseParser:
    """Parses and validates AI responses"""
    
    @staticmethod
    def parse(ai_text: str) -> Dict:
        """
        Parse JSON response from AI
        
        Args:
            ai_text: Raw AI response
        
        Returns:
            Validated dictionary
        """
        try:
            data = json.loads(ai_text)
        except json.JSONDecodeError:
            logger.warning("⚠️ JSON parsing failed, attempting recovery...")
            match = re.search(r"\{.*\}", ai_text, re.DOTALL)
            if not match:
                raise ValueError("Invalid JSON returned by AI")
            data = json.loads(match.group())
        
        # Validate decision field
        decision = data.get("decision")
        if decision not in {"pass", "fail"}:
            raise ValueError("Invalid or missing decision field")
        
        # Return normalized structure
        return {
            "decision": decision,
            "missing_fields": data.get("missing_fields", []),
            "incomplete_fields": data.get("incomplete_fields", []),
            "quality_issues": data.get("quality_issues", []),
            "rules_violations": data.get("rules_violations", []),
            "suggestions": data.get("suggestions", []),
            "field_improvements": data.get("field_improvements", {}),
            "overall_assessment": data.get("overall_assessment", ""),
            "language_detected": data.get("language_detected", "en")
        }


# ============================================================
# DATABASE STORAGE
# ============================================================

class ValidationStorage:
    """Handles database operations"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def store_validation(self, report_step_id: int, data: Dict):
        """
        Store validation result in step_validation table
        
        Uses UPSERT (INSERT ... ON CONFLICT UPDATE)
        """
        # Combine all issues for the issues column
        all_issues = (
            data.get("incomplete_fields", []) +
            data.get("quality_issues", []) +
            data.get("rules_violations", [])
        )
        
        payload = {
            "step_id": report_step_id,
            "decision": data["decision"],
            "missing": data.get("missing_fields", []),
            "issues": all_issues,
            "suggestions": data.get("suggestions", []),
            "rewrite": json.dumps(data.get("field_improvements", {})),
            "notes": data.get("overall_assessment", "")
        }
        
        query = text("""
            INSERT INTO step_validation
            (report_step_id, decision, missing, issues, suggestions,
             professional_rewrite, notes)
            VALUES (:step_id, :decision, :missing, :issues,
                    :suggestions, :rewrite, :notes)
            ON CONFLICT (report_step_id)
            DO UPDATE SET
                decision = EXCLUDED.decision,
                missing = EXCLUDED.missing,
                issues = EXCLUDED.issues,
                suggestions = EXCLUDED.suggestions,
                professional_rewrite = EXCLUDED.professional_rewrite,
                notes = EXCLUDED.notes,
                validated_at = CURRENT_TIMESTAMP
        """)
        
        self.db.execute(query, payload)
        logger.info("💾 Validation stored for report_step_id %d", report_step_id)
    
    def update_step_status(self, report_step_id: int, decision: str):
        """Update report_step status based on validation"""
        status = "validated" if decision == "pass" else "rejected"
        
        query = text("""
            UPDATE report_steps
            SET status = :status,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :step_id
        """)
        
        self.db.execute(query, {"step_id": report_step_id, "status": status})
        logger.info("📊 Step status updated to %s", status)


# ============================================================
# MAIN CHATBOT SERVICE
# ============================================================

class ChatbotService:
    """
    Main chatbot service orchestrator
    
    This is the entry point used by API routes
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.kb = KnowledgeBaseRetriever(db)
        self.prompt = PromptBuilder()
        self.ai = OpenAIClient()
        self.parser = ResponseParser()
        self.storage = ValidationStorage(db)
    
    def validate_step(
        self,
        report_step_id: int,
        step_code: str,
        step_data: Optional[Dict] = None
    ) -> Dict:
        """
        Main validation workflow with enriched context    
        Args:
            report_step_id: ID of report step
            step_code: Step code (D1-D8)
            step_data: User's response data (optional, will read from DB if not provided)
        
        Returns:
            Validation result dictionary
        """
        logger.info("🚀 Starting ENRICHED validation for %s (ID: %d)", 
                   step_code, report_step_id)
        
        # 🆕 NOUVEAU : Si step_data n'est pas fourni, le lire depuis la DB
        if step_data is None or not step_data:
            logger.info("📖 Reading step_data from database (report_steps.data)...")
            query = text("""
                SELECT data
                FROM report_steps
                WHERE id = :step_id
            """)
            result = self.db.execute(query, {"step_id": report_step_id}).fetchone()
            
            if not result:
                raise ValueError(f"Report step with id {report_step_id} not found")
            
            if not result[0]:
                raise ValueError(f"No data found in report_steps.data for report_step_id {report_step_id}. Please save the step first.")
            
            step_data = result[0]  # PostgreSQL JSONB est automatiquement parsé en dict
            logger.info("✅ Step data loaded from DB (%d fields)", len(step_data))
        else:
            logger.info("📝 Using step_data provided in request (%d fields)", len(step_data))
        
        # 1. Retrieve coaching content
        coaching = self.kb.get_step_coaching_content(step_code)
        
        # 2. Retrieve 20 Rules
        twenty_rules = self.kb.get_twenty_rules()
        
        # 3. Retrieve complaint context
        complaint = self.kb.get_complaint_context(report_step_id)
        
        # 4. Build enriched prompt
        prompt = self.prompt.build_enriched_validation_prompt(
            step_code=step_code,
            coaching=coaching,
            twenty_rules=twenty_rules,
            complaint=complaint,
            step_data=step_data
        )
        
        # 5. Call OpenAI
        ai_raw = self.ai.validate_step(prompt)
        
        # 6. Parse response
        validation = self.parser.parse(ai_raw)
        
        # 7. Store in database
        try:
            self.storage.store_validation(report_step_id, validation)
            self.storage.update_step_status(report_step_id, validation["decision"])
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("❌ Database transaction failed")
            raise
        
        logger.info("✅ Enriched validation completed: %s", validation["decision"])
        return validation
    
    def health_check(self) -> Dict:
        """Health check endpoint"""
        try:
            # Count coaching documents
            count = self.db.execute(text("""
                SELECT COUNT(*)
                FROM kb_chunks
                WHERE section_hint LIKE '%_coaching_validation'
            """)).scalar()
            
            # Check if 20 Rules exists
            rules_exist = self.db.execute(text("""
                SELECT EXISTS(
                    SELECT 1 FROM kb_chunks
                    WHERE section_hint = 'floor_rules_guidelines'
                )
            """)).scalar()
            
            return {
                "status": "healthy",
                "service": "chatbot",
                "kb_chunks_available": count,
                "twenty_rules_loaded": rules_exist,
                "message": "Service operational with enriched validation"
            }
        
        except Exception as e:
            logger.error("Health check failed: %s", str(e))
            return {
                "status": "unhealthy",
                "service": "chatbot",
                "kb_chunks_available": 0,
                "twenty_rules_loaded": False,
                "message": str(e)
            }