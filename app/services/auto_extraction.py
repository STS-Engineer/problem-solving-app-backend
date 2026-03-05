"""
auto_extraction_service.py
══════════════════════════
One-shot AI extraction triggered at complaint creation.

Responsibilities
────────────────
1. Receive the newly created Complaint ORM object + the list of ReportStep
   objects created alongside it.
2. Call the LLM once with ALL available complaint metadata.
3. Parse the structured JSON response.
4. Write the extracted data into the appropriate step.data fields:
     D2 <- problem_description, five_w_2h, standard_applicable,
            expected_situation, observed_situation, evidence_documents,
            is_is_not_factors
5. Flush — the caller (ComplaintService.create_complaint) commits.


Usage in complaint_service.py
------------------------------
    from app.services.auto_extraction_service import auto_fill_from_complaint

    # after db.commit() + db.refresh(complaint), before returning:
    steps = db.query(ReportStep).filter(ReportStep.report_id == report.id).all()
    auto_fill_from_complaint(db, complaint, steps)
    db.commit()
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.conversation_service import _merge_extracted  

logger = logging.getLogger(__name__)


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

_SYSTEM = """
You are a senior 8D Quality Engineer with 20+ years of experience in
industrial manufacturing — brushes, chokes, seals, and electro-mechanical
assemblies (motors, connectors, winding coils, cable harnesses).

A new quality complaint has just been created in the system. You receive its
full metadata. Your job is to reason over all available information — exactly
as a quality engineer would when first reading a complaint dossier — and
pre-populate as many 8D report fields as possible, so the user only needs to
confirm or fill the gaps.

=======================================
REASONING APPROACH
=======================================

Step 1 — READ EVERYTHING
  Absorb every field provided: title, description, defects label, product
  type, process linked to problem, application, customer plant, our plant,
  dates, repetitiveness indicator, quality issue type.

Step 2 — INFER AND REFORMULATE THE 5W2H
  Write each answer as a SHORT, CLEAR ENGINEERING SENTENCE — not raw data copy.
  Think: "how would a quality engineer write this in a meeting report?"
  NEVER invent or assume data. If a field cannot be determined from the
  complaint metadata, leave it as "".

  WHAT     : Describe the failure mode precisely.
             Format: "[Part] has [defect] at [location on part]."
             Example: "The RODCHOKE presents an absence of weld joint between
                       the thermal switch and the ground contact."
             If not determinable, leave "".

  WHERE    : Physical location where the defect was detected.
             Distinguish between: customer incoming inspection / customer
             production line / our plant / in transit — only if stated.
             If only the customer name is known, write:
             "Detected at [customer] plant."
             If not determinable, leave "".

WHEN     : Extract dates only from {customer_complaint_date}. Use ISO format YYYY-MM-DD.
           

  WHO      : Who detected or reported the defect.
             Write only what is explicitly stated — a person, role, or team.
             Do NOT infer "quality team" or any role from the company name alone.
             If not explicitly stated, leave "".

  WHY      : Why is this a problem? Link to functional impact.
             Format: "This defect causes [functional risk] in the
             [application], classified as [priority/quality type]."
             If not determinable, leave "".

  HOW      : Detection method only if explicitly stated in the description.
             Do NOT infer from defect type.
             If not stated, leave "".

  HOW MANY : Quantity of NOK parts only if explicitly stated.
             If not stated, leave "".
             
Step 3 — FILL D2 FIELDS
  problem_description : Write 2-3 sentences structured as follows:
  Sentence 1 — THE DEFECT: "[Product type] ([product line]) produced at 
               [our plant] for [customer] presents [defect] on [location]."
  Sentence 2 — THE IMPACT: "This defect [functional consequence], 
               preventing/affecting [application or next operation]."
  Sentence 3 — THE CONTEXT: "Issue linked to [process]; classified as 
               [priority] (quality type: [quality_issue_type]). 
               First occurrence (repetition: [repetitive_number]).
               
  five_w_2h           : fill all 7 sub-fields from your analysis.
  standard_applicable : extract only if a standard/WI/spec is explicitly named;
                        otherwise leave "".
  expected_situation  : what the part/process SHOULD deliver normally.
  observed_situation  : what was actually found — use defects field directly
                        if available, enriched by description.
  evidence_documents  : always "" at this stage (no files uploaded yet).
  is_is_not_factors   : populate Product and Time from available data.
                        Leave Lot and Pattern as "" unless explicitly stated.

Step 4 — FILL D3 HINTS (ONLY if explicitly stated in the description)
  defected_part_status    : set returned/isolated/identified to true ONLY if
                            the description explicitly says so
                            (e.g. "parts sent back", "parts quarantined",
                            "tagged for identification").
  suspected_parts_status  : add a location row ONLY if a specific location +
                            quantity is clearly stated
                            (e.g. "3 pieces still in transit to our plant").

=======================================
STRICT RULES
=======================================
- NEVER invent data. Only extract what is stated or clearly implied.
- NEVER add team_members — D1 is filled manually by the user.
- Use "" for unknown string fields, false for unknown boolean fields.
- Dates in ISO format YYYY-MM-DD if possible, otherwise keep original text.
- Return ONLY valid JSON. No markdown fences. No explanation. No comments.
"""


# =============================================================================
# USER MESSAGE TEMPLATE
# =============================================================================

_USER_TEMPLATE = """
=== COMPLAINT METADATA ===
Reference number          : {reference_number}
Title                     : {complaint_name}
Description               : {complaint_description}
Defects (label)           : {defects}
Quality issue type        : {quality_issue_warranty}
Number of repetition (happened before or not)          : {repetitive_complete_with_number}

--- Product ---
Product line              : {product_line}
Product type (ours)       : {avocarbon_product_type}
Concerned application     : {concerned_application}
Process linked to problem : {potential_avocarbon_process_linked_to_problem}

--- Parties ---
Customer                  : {customer}
Customer plant            : {customer_plant_name}
Our plant                 : {avocarbon_plant}

--- Dates ---
Customer complaint date   : {customer_complaint_date}
Complaint opening date    : {complaint_opening_date}
Due date                  : {due_date}

--- Classification ---
Priority                  : {priority}

=== YOUR TASK ===
Reason over every field above and return the following JSON structure.
Leave string fields as "" and boolean fields as false if you cannot
determine them. Do NOT add, remove, or rename any keys.

{{
  "problem_description": "",

  "five_w_2h": {{
    "what":     "",
    "where":    "",
    "when":     "",
    "who":      "",
    "why":      "",
    "how":      "",
    "how_many": ""
  }},

  "standard_applicable": "",
  "expected_situation":  "",
  "observed_situation":  "",



  "defected_part_status": {{
    "returned":          false,
    "isolated":          false,
    "isolated_location": "",
    "identified":        false,
    "identified_method": ""
  }},

  "suspected_parts_status": []
}}
"""


# =============================================================================
# ROUTING MAP  (keys -> step_code, aligned with section_config.py)
# =============================================================================

_KEY_TO_STEP: Dict[str, str] = {
    # D2 — five_w_2h section
    "problem_description":  "D2",
    "five_w_2h":            "D2",
    # D2 — deviation section
    "standard_applicable":  "D2",
    "expected_situation":   "D2",
    "observed_situation":   "D2",
    # D3 — containment section
    "defected_part_status":     "D3",
    "suspected_parts_status":   "D3",
}


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _get(obj: Any, key: str, default: str = "") -> str:
    """
    Safe attribute/dict accessor.
    Handles ORM objects, plain dicts, SQLAlchemy/Python enums, dates, None.
    """
    if isinstance(obj, dict):
        val = obj.get(key, default)
    else:
        val = getattr(obj, key, default)

    if val is None:
        return default
    if hasattr(val, "value"):       # enum
        return str(val.value)
    if hasattr(val, "isoformat"):   # date / datetime
        return val.isoformat()
    return str(val)


def _drop_empty(obj: Any) -> Any:
    """
    Recursively remove empty strings, None, empty dicts, and empty lists.
    Boolean False is intentionally KEPT so defected_part_status flags are
    preserved even when false.
    """
    if isinstance(obj, dict):
        cleaned = {k: _drop_empty(v) for k, v in obj.items()}
        return {
            k: v for k, v in cleaned.items()
            if not (v == "" or v is None or v == {} or v == [])
        }
    if isinstance(obj, list):
        cleaned = [_drop_empty(i) for i in obj]
        return [i for i in cleaned if not (i == "" or i is None or i == {} or i == [])]
    return obj


def _build_user_message(complaint: Any) -> str:
    return _USER_TEMPLATE.format(
        reference_number                          = _get(complaint, "reference_number"),
        complaint_name                            = _get(complaint, "complaint_name"),
        complaint_description                     = _get(complaint, "complaint_description"),
        defects                                   = _get(complaint, "defects"),
        quality_issue_warranty                    = _get(complaint, "quality_issue_warranty"),
        repetitive_complete_with_number           = _get(complaint, "repetitive_complete_with_number"),
        product_line                              = _get(complaint, "product_line"),
        avocarbon_product_type                    = _get(complaint, "avocarbon_product_type"),
        concerned_application                     = _get(complaint, "concerned_application"),
        potential_avocarbon_process_linked_to_problem = _get(
            complaint, "potential_avocarbon_process_linked_to_problem"
        ),
        customer                                  = _get(complaint, "customer"),
        customer_plant_name                       = _get(complaint, "customer_plant_name"),
        avocarbon_plant                           = _get(complaint, "avocarbon_plant"),
        customer_complaint_date                   = _get(complaint, "customer_complaint_date"),
        complaint_opening_date                    = _get(complaint, "complaint_opening_date"),
        due_date                                  = _get(complaint, "due_date"),
        priority                                  = _get(complaint, "priority"),
    )


def _step_id_by_code(steps: list) -> Dict[str, int]:
    """Build {step_code: step.id} lookup from a list of ReportStep objects."""
    return {s.step_code: s.id for s in steps}


def _route_to_steps(
    extracted: Dict[str, Any],
    db: Session,
    steps: list,
) -> None:
    """
    Distribute extracted keys into the correct ReportStep.data.
    Uses _KEY_TO_STEP routing and _merge_extracted for safe deep-merging.
    Only flushes — caller is responsible for committing.
    """
    from app.models.report_step import ReportStep  # avoid circular import
    from datetime import datetime, timezone

    code_to_id = _step_id_by_code(steps)

    # Group all extracted keys by their target step_code
    step_payloads: Dict[str, Dict[str, Any]] = {}
    for key, value in extracted.items():
        step_code = _KEY_TO_STEP.get(key)
        if step_code is None:
            logger.debug("auto_fill: skipping unmapped key '%s'", key)
            continue
        step_payloads.setdefault(step_code, {})[key] = value

    for step_code, payload in step_payloads.items():
        step_id = code_to_id.get(step_code)
        if step_id is None:
            logger.warning("auto_fill: step_code '%s' not in created steps", step_code)
            continue

        step = db.get(ReportStep, step_id)
        if step is None:
            logger.warning("auto_fill: ReportStep id=%s not found in DB", step_id)
            continue

        current      = step.data or {}
        step.data    = _merge_extracted(current, payload)
        step.updated_at = datetime.now(timezone.utc)
        db.flush()

        logger.info(
            "auto_fill: step %d (%s) <- %s",
            step_id, step_code, list(payload.keys()),
        )


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

def auto_fill_from_complaint(
    db: Session,
    complaint: Any,
    steps: list,
) -> Optional[Dict[str, Any]]:
    """
    One-shot LLM call to pre-fill 8D step data from complaint metadata.

    Parameters
    ----------
    db        : active SQLAlchemy session  (caller commits after this returns)
    complaint : Complaint ORM instance     (already committed — has .id)
    steps     : list of ReportStep ORM instances for this report

    Returns the raw extracted dict (for logging/audit), or None on failure.
    Failures are NON-FATAL — the complaint and steps are already committed.
    """
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    try:
        user_msg = _build_user_message(complaint)

        response = client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,   # deterministic fact-based extraction
            max_completion_tokens=1500,
            timeout=30,
        )
        
        # logger.info(
        #     "system content  %s", _SYSTEM)
        # logger.info("****************************")
        # logger.info("user content    %s", user_msg)
        # logger.info("****************************")
        # raw = response.choices[0].message.content.strip()
        # logger.info("************************************************************************")
        # logger.info("raw LLM response: %s", raw)


        # Strip any accidental markdown code fences
        raw = re.sub(r"^```[a-z]*\s*", "", raw, flags=re.MULTILINE)
        raw = raw.strip("` \n")

        extracted: Dict[str, Any] = json.loads(raw)
        extracted = _drop_empty(extracted)

        if not extracted:
            logger.info(
                "auto_fill: nothing extracted for complaint %s",
                _get(complaint, "reference_number"),
            )
            return None

        _route_to_steps(extracted, db, steps)

        logger.info(
            "auto_fill: complaint %s pre-filled with keys: %s",
            _get(complaint, "reference_number"),
            list(extracted.keys()),
        )
        return extracted

    except OpenAIError as exc:
        logger.warning(
            "auto_fill: OpenAI error [%s]: %s",
            _get(complaint, "reference_number"), exc,
        )
        return None
    except json.JSONDecodeError as exc:
        logger.warning(
            "auto_fill: JSON parse error [%s]: %s",
            _get(complaint, "reference_number"), exc,
        )
        return None
    except Exception as exc:
        logger.warning(
            "auto_fill: unexpected error [%s]: %s",
            _get(complaint, "reference_number"), exc,
        )
        return None