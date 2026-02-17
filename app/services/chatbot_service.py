"""Chatbot Service - AI-Powered Step Validation
Version: 2.2 - Fixed ValidationStorage (ORM replaces broken raw SQL)

KEY FIX: ValidationStorage.store_validation() previously called json.dumps()
on Python lists before binding them to ARRAY(Text) PostgreSQL columns.
psycopg2 received strings like '["item"]' instead of Python lists, causing a
type-cast ProgrammingError that poisoned the entire SQLAlchemy session with
InFailedSqlTransaction on every subsequent statement.

Fix: Use the SQLAlchemy ORM directly. SQLAlchemy knows the column type is
ARRAY(Text) and instructs psycopg2 to bind Python lists correctly.
Only professional_rewrite (a plain TEXT column) correctly uses json.dumps.
"""

import json
import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import text
from openai import OpenAI, OpenAIError

from app.core.config import settings
from app.models.step_validation import StepValidation  # ORM model

logger = logging.getLogger(__name__)


# ============================================================
# D1 LOCAL VALIDATOR  (no KB, no OpenAI)
# ============================================================

VALID_ROLES = {
    "production", "maintenance", "engineering",
    "logistics", "team_leader", "other"
}

REQUIRED_ROLES = {"team_leader"}
REQUIRED_MEMBER_FIELDS = ["name", "function", "department", "role"]


class D1LocalValidator:
    """
    Validates D1 (Establish the Team) entirely locally.
    No Knowledge Base lookup, no OpenAI call needed.
    """

    def validate(self, step_data: Dict) -> Dict:
        missing_fields: List[str] = []
        incomplete_fields: List[str] = []
        quality_issues: List[str] = []
        suggestions: List[str] = []
        field_improvements: Dict[str, str] = {}

        members = step_data.get("team_members")

        if not isinstance(members, list):
            missing_fields.append("team_members")
            return self._build_result(
                decision="fail",
                missing_fields=missing_fields,
                overall_assessment="team_members field is missing or not a list."
            )

        if len(members) < 2:
            incomplete_fields.append(
                "team_members: at least 2 members are required for a valid 8D team"
            )
            suggestions.append(
                "Add more cross-functional members (e.g. Quality, Production, Engineering)."
            )

        seen_names: List[str] = []
        leader_count = 0

        for idx, member in enumerate(members):
            label = f"Member #{idx + 1}"
            if not isinstance(member, dict):
                incomplete_fields.append(f"{label}: must be a dict/object")
                continue

            for field in REQUIRED_MEMBER_FIELDS:
                value = member.get(field, "")
                if not isinstance(value, str) or not value.strip():
                    incomplete_fields.append(f"{label}: '{field}' is empty or missing")

            role = member.get("role", "")
            if role and role not in VALID_ROLES:
                quality_issues.append(
                    f"{label}: role '{role}' is invalid. "
                    f"Must be one of: {', '.join(sorted(VALID_ROLES))}"
                )
                field_improvements[f"member_{idx+1}_role"] = (
                    f"Use one of: {', '.join(sorted(VALID_ROLES))}"
                )

            if role == "team_leader":
                leader_count += 1

            name = member.get("name", "").strip().lower()
            if name:
                if name in seen_names:
                    quality_issues.append(
                        f"{label}: duplicate name '{member.get('name')}' detected"
                    )
                else:
                    seen_names.append(name)

        if leader_count == 0 and len(members) >= 2:
            incomplete_fields.append(
                "team_members: no team_leader assigned — "
                "one member must have role = 'team_leader'"
            )
            suggestions.append(
                "Assign the role 'team_leader' to the person responsible "
                "for driving the 8D process."
            )
        elif leader_count > 1:
            quality_issues.append(
                f"team_members: {leader_count} members have role 'team_leader' — "
                "only one team leader is allowed"
            )

        has_issues = bool(missing_fields or incomplete_fields or quality_issues)
        decision = "fail" if has_issues else "pass"

        if decision == "pass":
            overall = (
                f"D1 validated ✅ — {len(members)} team member(s) correctly defined "
                f"with all required fields and a designated team leader."
            )
        else:
            total = len(incomplete_fields) + len(quality_issues) + len(missing_fields)
            overall = (
                f"D1 needs {total} correction(s) before it can be approved. "
                "Please fix the issues listed above."
            )

        return self._build_result(
            decision=decision,
            missing_fields=missing_fields,
            incomplete_fields=incomplete_fields,
            quality_issues=quality_issues,
            suggestions=suggestions,
            field_improvements=field_improvements,
            overall_assessment=overall,
        )

    @staticmethod
    def _build_result(
        decision: str,
        missing_fields: List[str] = None,
        incomplete_fields: List[str] = None,
        quality_issues: List[str] = None,
        rules_violations: List[str] = None,
        suggestions: List[str] = None,
        field_improvements: Dict[str, str] = None,
        overall_assessment: str = "",
        language_detected: str = "en",
    ) -> Dict:
        return {
            "decision": decision,
            "missing_fields": missing_fields or [],
            "incomplete_fields": incomplete_fields or [],
            "quality_issues": quality_issues or [],
            "rules_violations": rules_violations or [],
            "suggestions": suggestions or [],
            "field_improvements": field_improvements or {},
            "overall_assessment": overall_assessment,
            "language_detected": language_detected,
        }


# ============================================================
# KNOWLEDGE BASE RETRIEVER
# ============================================================

class KnowledgeBaseRetriever:
    """Retrieves all knowledge from database"""

    def __init__(self, db: Session):
        self.db = db

    def get_step_coaching_content(self, step_code: str) -> str:
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
        logger.warning("⚠️ 20 Rules not found in KB")
        return ""

    def get_complaint_context(self, report_step_id: int) -> Dict:
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
            logger.info("📋 Complaint context loaded: %s", context["complaint_name"])
            return context
        logger.warning("⚠️ No complaint found for report_step_id %d", report_step_id)
        return {}


# ============================================================
# PROMPT BUILDER
# ============================================================

class PromptBuilder:
    """Builds enriched validation prompts"""

    @staticmethod
    def format_step_data(step_data: Dict) -> str:
        if not step_data:
            return "No data provided"
        return "\n".join(
            f"{key.replace('_', ' ').title()}: {value}"
            for key, value in step_data.items()
        )

    @staticmethod
    def format_complaint_context(complaint: Dict) -> str:
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
        formatted_complaint = PromptBuilder.format_complaint_context(complaint)
        formatted_data = PromptBuilder.format_step_data(step_data)

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
            raise RuntimeError(f"OpenAI validation failed: {str(e)}")


# ============================================================
# RESPONSE PARSER
# ============================================================

class ResponseParser:
    """Parses and validates AI responses"""

    @staticmethod
    def parse(ai_text: str) -> Dict:
        try:
            data = json.loads(ai_text)
        except json.JSONDecodeError:
            logger.warning("⚠️ JSON parsing failed, attempting recovery...")
            match = re.search(r"\{.*\}", ai_text, re.DOTALL)
            if not match:
                raise ValueError("Invalid JSON returned by AI")
            data = json.loads(match.group())

        decision = data.get("decision")
        if decision not in {"pass", "fail"}:
            raise ValueError("Invalid or missing decision field")

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
# DATABASE STORAGE  ← THE FIXED CLASS
# ============================================================

class ValidationStorage:
    """
    Handles ORM-based upsert of step validation results.

    ── WHY THE OLD VERSION FAILED ──────────────────────────────────────────────
    The previous implementation used raw SQL and called json.dumps() on every
    list before binding:

        "missing": json.dumps(missing_fields)   →  '["what", "where"]'  (str)

    The StepValidation model declares these columns as ARRAY(Text).
    psycopg2 receives a Python str where it expects a list, raises a
    ProgrammingError, which aborts the PostgreSQL transaction.  Every following
    statement in the same Session then fails with InFailedSqlTransaction.

    ── FIX ─────────────────────────────────────────────────────────────────────
    Use the SQLAlchemy ORM. When you assign a Python list to an ARRAY(Text)
    mapped attribute, SQLAlchemy tells psycopg2 the correct type and it binds
    it properly. No json.dumps on lists — ever.

    Only `professional_rewrite` is a plain TEXT column that intentionally
    stores a JSON string, so json.dumps() is correct there.
    ────────────────────────────────────────────────────────────────────────────
    """

    def __init__(self, db: Session):
        self.db = db

    def store_validation(self, report_step_id: int, data: Dict) -> None:
        """
        Upsert a StepValidation row via the ORM.

        Column mapping (StepValidation model):
            missing              ARRAY(Text)  ← Python list[str], no json.dumps
            issues               ARRAY(Text)  ← Python list[str], no json.dumps
            suggestions          ARRAY(Text)  ← Python list[str], no json.dumps
            professional_rewrite Text         ← json.dumps(dict) is correct here
            notes                Text         ← plain str
            decision             String       ← 'pass' | 'fail'
        """
        # ── Extract as plain Python lists ────────────────────────────────────
        missing_fields    = list(data.get("missing_fields", []))
        incomplete_fields = list(data.get("incomplete_fields", []))
        quality_issues    = list(data.get("quality_issues", []))
        rules_violations  = list(data.get("rules_violations", []))
        suggestions       = list(data.get("suggestions", []))
        field_improvements = data.get("field_improvements", {})
        overall_assessment = str(data.get("overall_assessment", ""))

        # Merge all issue categories → single ARRAY(Text) `issues` column
        combined_issues: list = incomplete_fields + quality_issues + rules_violations

        # professional_rewrite is TEXT — JSON string is intentional here
        rewrite_json: str = json.dumps(field_improvements, ensure_ascii=False)

        # ── ORM upsert ────────────────────────────────────────────────────────
        existing: Optional[StepValidation] = (
            self.db.query(StepValidation)
            .filter(StepValidation.report_step_id == report_step_id)
            .first()
        )

        if existing:
            existing.decision             = data["decision"]
            existing.missing              = missing_fields    # list → ARRAY(Text) ✅
            existing.issues               = combined_issues   # list → ARRAY(Text) ✅
            existing.suggestions          = suggestions       # list → ARRAY(Text) ✅
            existing.professional_rewrite = rewrite_json      # str  → Text       ✅
            existing.notes                = overall_assessment
            existing.validated_at         = datetime.now(timezone.utc)
            logger.info(
                "🔄 Validation updated for step_id=%d decision=%s",
                report_step_id, data["decision"],
            )
        else:
            new_row = StepValidation(
                report_step_id        = report_step_id,
                decision              = data["decision"],
                missing               = missing_fields,    # list → ARRAY(Text) ✅
                issues                = combined_issues,   # list → ARRAY(Text) ✅
                suggestions           = suggestions,       # list → ARRAY(Text) ✅
                professional_rewrite  = rewrite_json,      # str  → Text       ✅
                notes                 = overall_assessment,
                validated_at          = datetime.now(timezone.utc),
            )
            self.db.add(new_row)
            logger.info(
                "💾 Validation created for step_id=%d decision=%s",
                report_step_id, data["decision"],
            )

        # The caller (ChatbotService.validate_step) calls db.commit() — don't commit here.


# ============================================================
# STEPS THAT USE LOCAL VALIDATION (no KB / no OpenAI)
# ============================================================

LOCAL_VALIDATION_STEPS = {"D1"}


# ============================================================
# MAIN CHATBOT SERVICE
# ============================================================

class ChatbotService:
    """
    Main chatbot service orchestrator.
    Routes D1 to local validation; all other steps go through OpenAI.
    """

    def __init__(self, db: Session):
        self.db = db
        self.kb = KnowledgeBaseRetriever(db)
        self.prompt = PromptBuilder()
        self.ai = OpenAIClient()
        self.parser = ResponseParser()
        self.storage = ValidationStorage(db)
        self.d1_validator = D1LocalValidator()

    def validate_step(
        self,
        report_step_id: int,
        step_code: str,
        step_data: Optional[Dict] = None
    ) -> Dict:
        """
        Main validation workflow.

        - D1  → local rule-based validation (no KB, no OpenAI)
        - D2–D8 → enriched AI validation via OpenAI
        """
        logger.info("🚀 Starting validation for %s (ID: %d)", step_code, report_step_id)

        # Load step_data from DB if not provided
        if not step_data:
            logger.info("📖 Reading step_data from database...")
            query = text("SELECT data FROM report_steps WHERE id = :step_id")
            result = self.db.execute(query, {"step_id": report_step_id}).fetchone()

            if not result:
                raise ValueError(f"Report step {report_step_id} not found")
            if not result[0]:
                raise ValueError(
                    f"No data in report_steps.data for step {report_step_id}. "
                    "Please save the step first."
                )
            step_data = result[0]
            logger.info("✅ Step data loaded from DB (%d fields)", len(step_data))
        else:
            logger.info("📝 Using step_data from request (%d fields)", len(step_data))

        # Route: D1 → local, everything else → OpenAI
        if step_code in LOCAL_VALIDATION_STEPS:
            validation = self._validate_locally(step_code, step_data)
        else:
            validation = self._validate_with_ai(step_code, report_step_id, step_data)

        # Persist result
        try:
            self.storage.store_validation(report_step_id, validation)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("❌ DB transaction failed during store_validation")
            raise

        logger.info("✅ Validation completed: %s", validation["decision"])
        return validation

    def _validate_locally(self, step_code: str, step_data: Dict) -> Dict:
        logger.info("🔍 Running LOCAL validation for %s (no KB / no OpenAI)", step_code)
        if step_code == "D1":
            return self.d1_validator.validate(step_data)
        raise ValueError(f"No local validator implemented for {step_code}")

    def _validate_with_ai(
        self,
        step_code: str,
        report_step_id: int,
        step_data: Dict
    ) -> Dict:
        logger.info("🤖 Running AI validation for %s", step_code)

        coaching = self.kb.get_step_coaching_content(step_code)
        twenty_rules = self.kb.get_twenty_rules()
        complaint = self.kb.get_complaint_context(report_step_id)

        prompt = self.prompt.build_enriched_validation_prompt(
            step_code=step_code,
            coaching=coaching,
            twenty_rules=twenty_rules,
            complaint=complaint,
            step_data=step_data
        )

        ai_raw = self.ai.validate_step(prompt)
        return self.parser.parse(ai_raw)

    def health_check(self) -> Dict:
        try:
            count = self.db.execute(text("""
                SELECT COUNT(*) FROM kb_chunks
                WHERE section_hint LIKE '%_coaching_validation'
            """)).scalar()

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
                "message": "Service operational — D1 uses local validation, D2-D8 use AI"
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