# app/services/conversation_service.py
"""
Conversational coaching service.

Flow per section
────────────────
1. User arrives on section  →  GET /conversation  →  bot sends opening question
2. User answers             →  POST /conversation  →  bot parses, asks follow-up
3. When enough data         →  bot embeds <extracted_fields>{...}</extracted_fields>
                               extracted fields are immediately merged into step.data
4. When ALL sections of the step are complete → step.status set to 'fulfilled'

File uploads
────────────
Files are uploaded separately via POST /steps/{step_id}/files (step_files router).
The conversation service receives a list of already-uploaded StepFile records
(their filenames) so the AI can reference them in its reasoning.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.step_conversation import StepConversation
from app.models.step_file import StepFile
from app.models.file import File as FileModel
from app.services.section_config import get_all_section_keys

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION OPENING MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

SECTION_OPENING: Dict[str, str] = {
    # ── D1 ────────────────────────────────────────────────────────────────────
    "team_members": (
        "👋 Let's build your **D1 — Team** together.\n\n"
        "Tell me who is on the 8D team. For each person give me their "
        "**name**, ***Email address**, **department** (Production / Quality / Engineering / Maintenance / Logistics / Supplier Quality / Other) "
        "and **function** (Operator / Line Leader / Supervisor / Engineer / Team Leader / Project Manager / Other).\n\n"
        "You can list everyone at once or one by one — whichever is easier.\n\n"
          "Start with the first member. Once you have provided at least 2 members "
        "and identified the team leader, I will complete the section automatically."
    ),

    # ── D2 ────────────────────────────────────────────────────────────────────
    "five_w_2h": (
        "👋 Let's fill in the **5W2H analysis** for your problem description.\n\n"
        "Start by giving me a short description of the problem "
        "(object/process + defect observed). Then I'll guide you through the "
        "7 questions: What, Where, When, Who, Why, How, and How Many.\n\n"
        "What is the problem?"
    ),
    "deviation": (
        "📋 Now let's document the **deviation vs the standard**.\n\n"
        "Please tell me:\n"
        "1. What **standard or specification** applies here? (e.g. WI-WELD-02)\n"
        "2. What was the **expected situation**?\n"
        "3. What did you **actually observe**?\n"
        "4. Any **evidence documents** to reference? "
        "(You can also attach files using the 📎 button — I'll register them automatically)"
    ),
    "is_is_not": (
        "🔍 Let's build the **IS / IS NOT analysis**.\n\n"
        "For each factor below, tell me what IS affected and what is NOT:\n"
        "- **Product**: which product/part is affected vs not?\n"
        "- **Time**: when did it start / when did it not occur?\n"
        "- **Lot**: which lot numbers are affected vs not?\n"
        "- **Pattern**: is the defect on all units or a subset?\n\n"
        "You can answer all at once or one factor at a time."
    ),

    # ── D3 ────────────────────────────────────────────────────────────────────
    "containment": (
        "🛡️ Let's document your **D3 — Containment Actions**.\n\n"
        "Tell me about the defective parts:\n"
        "- Were they **returned**? **Isolated** (where)? **Identified** to avoid mishandling (how)?\n\n"
        "Then for suspected parts, walk me through each location "
        "(supplier site, in transit, production floor, warehouse, customer site):\n"
        "- How many parts? What actions were taken? Who led it? What was the result?\n\n"
        "Finally, who was the alert **communicated to**, and what is the alert number?"
    ),
    "restart": (
        "🔄 Now let's cover the **production restart** after containment.\n\n"
        "Please provide:\n"
        "1. **When** did production restart? (date, time, lot number)\n"
        "2. What was the **first certified lot**?\n"
        "3. Who **approved** the restart?\n"
        "4. What **verification method** was used?\n"
        "5. How were parts and boxes **identified**?\n"
        "6. Who is the overall **containment responsible**?"
    ),

    # ── D4 ────────────────────────────────────────────────────────────────────
    "four_m_occurrence": (
        "🔬 Let's analyse the **root cause of OCCURRENCE** (why the defect happened).\n\n"
        "Walk me through potential causes for each of the 4M + Environment:\n"
        "- **Material**: raw material or component issues?\n"
        "- **Method**: process or procedure problems?\n"
        "- **Machine**: equipment or tooling failures?\n"
        "- **Manpower**: operator skill, training, or error?\n"
        "- **Environment**: temperature, humidity, contamination?\n\n"
        "Then we'll do the **5 Whys** to drill down to the root cause.\n\n"
        "What do you suspect caused the defect?"
    ),
    "four_m_non_detection": (
        "🔎 Now let's analyse the **root cause of NON-DETECTION** "
        "(why the defect was not caught before reaching the customer).\n\n"
        "Same 4M + Environment approach — but focused on the detection system:\n"
        "- **Method**: inspection instructions, control plan gaps?\n"
        "- **Machine**: gauge, sensor, or test equipment issues?\n"
        "- **Manpower**: inspector awareness or training?\n"
        "- **Material**: sample size, traceability?\n"
        "- **Environment**: lighting, ergonomics at inspection station?\n\n"
        "What failed in the detection system?"
    ),

    # ── D5 ────────────────────────────────────────────────────────────────────
    "corrective_occurrence": (
        "✅ Let's plan **corrective actions targeting OCCURRENCE** "
        "(the root cause identified in D4).\n\n"
        "For each action, I need:\n"
        "- The **action description** (what will be done)\n"
        "- The **responsible person** (name)\n"
        "- The **due date**\n\n"
        "List as many actions as needed. What is the first corrective action?"
    ),
    "corrective_detection": (
        "🔍 Now let's plan **corrective actions targeting NON-DETECTION** "
        "(to improve your detection system).\n\n"
        "Same format — for each action:\n"
        "- **Action description**\n"
        "- **Responsible person**\n"
        "- **Due date**\n\n"
        "What is the first detection improvement action?"
    ),

    # ── D6 ────────────────────────────────────────────────────────────────────
    "implementation": (
        "🚀 Let's document the **implementation** of your corrective actions from D5.\n\n"
        "For each action, I need the:\n"
        "- **Implementation date** (actual date it was done)\n"
        "- **Evidence** (file name, document reference, photo, etc.)\n\n"
        "Walk me through each occurrence and detection action — were they implemented on time?"
    ),
    "monitoring_checklist": (
        "📊 Now let's fill in **monitoring & effectiveness verification**.\n\n"
        "Tell me:\n"
        "1. **Monitoring interval** (e.g. 3 shifts, 1 week)\n"
        "2. **Pieces produced** during monitoring\n"
        "3. **Rejection rate** observed\n"
        "4. Shift data (shift 1, shift 2)\n\n"
        "Then we'll go through the **implementation checklist** by shift. "
        "Who **audited** the implementation, and on what date?"
    ),

    # ── D7 ────────────────────────────────────────────────────────────────────
    "prevention": (
        "🔒 Let's document **prevention & replication** actions.\n\n"
        "First, **risk of recurrence elsewhere**:\n"
        "- Are there similar risks on other lines, products, or sites?\n"
        "- For each: area/line/product, is the risk present (yes/no/unknown), and what action was taken?\n\n"
        "Then, **replication validation**:\n"
        "- Where was the fix replicated (line/site)?\n"
        "- What action was replicated, how was it confirmed, and by whom?\n\n"
        "Start with the recurrence risks."
    ),
    "knowledge": (
        "📚 Let's update the **knowledge base** and set up **long-term monitoring**.\n\n"
        "Knowledge base updates:\n"
        "- What documents were updated? (Control Plan, PFMEA, WI, etc.)\n"
        "- Topic/reference, owner, and where it's stored?\n\n"
        "Long-term monitoring checkpoints:\n"
        "- What checkpoint type? Frequency? Owner? Start date? Any notes?\n\n"
        "What documents were updated as a result of this 8D?"
    ),
    "lessons_learned": (
        "📖 Finally, let's capture the **lessons learned**.\n\n"
        "Dissemination — for each audience/team:\n"
        "- Who was informed? By what method? On what date? Who owned it? Evidence?\n\n"
        "Then write a **lessons learned conclusion**: a summary of what happened, "
        "what was done, and what the team learned to prevent recurrence.\n\n"
        "Who did you share these lessons with?"
    ),

    # ── D8 ────────────────────────────────────────────────────────────────────
    "closure": (
        "🏁 We're at the final step — **D8 Closure**.\n\n"
        "Please write a comprehensive **closure statement** (at least 200 characters) addressing:\n"
        "- Is the customer satisfied?\n"
        "- Has the problem not recurred?\n"
        "- Has the team learned and improved?\n"
        "- Are all actions documented, visible, and stabilised?\n\n"
        "Then provide:\n"
        "- **Closed by** (name and title)\n"
        "- **Closure date**\n"
        "- **Approved by** (Quality Manager) and approval date (optional)\n\n"
        "Go ahead with the closure statement."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# JSON EXTRACTION SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

EXTRACTION_SCHEMA: Dict[str, str] = {
    # ── D1 ────────────────────────────────────────────────────────────────────
    "team_members": """{
  "team_members": [
    {
      "name":       "<string — full name of the person>",
      "department": "<string — MUST be exactly one of: production | maintenance | engineering | quality | logistics | supplier_quality | other>",
      "function":   "<string — MUST be exactly one of: operator | line_leader | supervisor | engineer | team_leader | project_manager | other>"
    }
  ]
}

CRITICAL FIELD NAMING RULES — follow exactly or the form will break:
- Use "department" NOT "dept" or "department_name"
- Use "function" NOT "role" or "job_title" or "position"
- department values: production | maintenance | engineering | quality | logistics | supplier_quality | other
- function values: operator | line_leader | supervisor | engineer | team_leader | project_manager | other
- If you cannot map to an exact value above, use "other"
- Return ALL team members collected so far (full accumulated list)
- Minimum 2 members required before extracting
- At least one member must have function = "team_leader"
- NEVER invent data not provided by the user""",

    # ── D2 ────────────────────────────────────────────────────────────────────
    "five_w_2h": """{
  "problem_description": "<string — 1-2 sentence description>",
  "five_w_2h": {
    "what":     "<string — the defect>",
    "where":    "<string — location: site / line / process>",
    "when":     "<string — ISO date or descriptive period>",
    "who":      "<string — who detected it>",
    "why":      "<string — why it is a problem>",
    "how":      "<string — how it was detected>",
    "how_many": "<string — quantity with unit>"
  }
}
All fields including all 7 five_w_2h sub-fields are REQUIRED before you extract.""",

    "deviation": """{
  "standard_applicable": "<string — standard name/code>",
  "expected_situation":  "<string — what should have been>",
  "observed_situation":  "<string — what was actually found>",
  "evidence_documents":  "<string — filenames of attached evidence, comma-separated, or empty string>"
}
standard_applicable, expected_situation, observed_situation are REQUIRED.
For evidence_documents: use the filenames from any attached files the user mentioned.""",

    "is_is_not": """{
  "is_is_not_factors": [
    {"factor": "Product", "is_problem": "<string>", "is_not_problem": "<string>"},
    {"factor": "Time",    "is_problem": "<string>", "is_not_problem": "<string>"},
    {"factor": "Lot",     "is_problem": "<string>", "is_not_problem": "<string>"},
    {"factor": "Pattern", "is_problem": "<string>", "is_not_problem": "<string>"}
  ]
}
At least 3 of 4 factors must have both is_problem and is_not_problem filled.""",

    # ── D3 ────────────────────────────────────────────────────────────────────
    "containment": """{
  "defected_part_status": {
    "returned":            <true|false>,
    "isolated":            <true|false>,
    "isolated_location":   "<string — location if isolated, else empty>",
    "identified":          <true|false>,
    "identified_method":   "<string — method if identified, else empty>"
  },
  "suspected_parts_status": [
    {
      "location":  "<string — one of: supplier_site | in_transit | production_floor | warehouse | customer_site | others>",
      "inventory": "<string — quantity>",
      "actions":   "<string — actions taken>",
      "leader":    "<string — responsible name>",
      "results":   "<string — outcome/status>"
    }
  ],
  "alert_communicated_to": {
    "production_shift_leaders": <true|false>,
    "quality_control":          <true|false>,
    "warehouse":                <true|false>,
    "maintenance":              <true|false>,
    "customer_contact":         <true|false>,
    "production_planner":       <true|false>
  },
  "alert_number": "<string — QRQC log or NCR reference>"
}
REQUIRED: defected_part_status must have at least one true field OR at least one
suspected_parts_status row with actions filled, AND alert_communicated_to must
have at least one true field or alert_number filled.
Only include suspected_parts_status rows the user mentioned — omit empty locations.""",

    "restart": """{
  "restart_production": {
    "when":                "<string — date, time, lot number>",
    "first_certified_lot": "<string — lot number>",
    "approved_by":         "<string — name and title>",
    "method":              "<string — verification method description>",
    "identification":      "<string — parts and boxes identification method>"
  },
  "containment_responsible": "<string — name and title of responsible person>"
}
REQUIRED: when, approved_by, and containment_responsible must be filled.""",

    # ── D4 ────────────────────────────────────────────────────────────────────
    "four_m_occurrence": """{
  "four_m_occurrence": {
    "row_1": {"material": "<string>", "method": "<string>", "machine": "<string>", "manpower": "<string>", "environment": "<string>"},
    "row_2": {"material": "<string>", "method": "<string>", "machine": "<string>", "manpower": "<string>", "environment": "<string>"},
    "row_3": {"material": "<string>", "method": "<string>", "machine": "<string>", "manpower": "<string>", "environment": "<string>"},
    "selected_problem": "<string — the identified root cause from the 4M analysis>"
  },
  "five_whys_occurrence": {
    "why_1": {"question": "<string>", "answer": "<string>"},
    "why_2": {"question": "<string>", "answer": "<string>"},
    "why_3": {"question": "<string>", "answer": "<string>"},
    "why_4": {"question": "<string>", "answer": "<string>"},
    "why_5": {"question": "<string>", "answer": "<string>"}
  },
  "root_cause_occurrence": {
    "root_cause":        "<string — final root cause statement>",
    "validation_method": "<string — how was the root cause validated>"
  }
}
REQUIRED: four_m_occurrence.selected_problem, root_cause_occurrence.root_cause,
root_cause_occurrence.validation_method, and at least 3 why entries must be filled.
Fill all four_m rows with what the user provides; use empty strings for unknowns.""",

    "four_m_non_detection": """{
  "four_m_non_detection": {
    "row_1": {"material": "<string>", "method": "<string>", "machine": "<string>", "manpower": "<string>", "environment": "<string>"},
    "row_2": {"material": "<string>", "method": "<string>", "machine": "<string>", "manpower": "<string>", "environment": "<string>"},
    "row_3": {"material": "<string>", "method": "<string>", "machine": "<string>", "manpower": "<string>", "environment": "<string>"},
    "selected_problem": "<string — identified non-detection root cause>"
  },
  "five_whys_non_detection": {
    "why_1": {"question": "<string>", "answer": "<string>"},
    "why_2": {"question": "<string>", "answer": "<string>"},
    "why_3": {"question": "<string>", "answer": "<string>"},
    "why_4": {"question": "<string>", "answer": "<string>"},
    "why_5": {"question": "<string>", "answer": "<string>"}
  },
  "root_cause_non_detection": {
    "root_cause":        "<string — final root cause of non-detection>",
    "validation_method": "<string — how the non-detection root cause was validated>"
  }
}
REQUIRED: four_m_non_detection.selected_problem, root_cause_non_detection.root_cause,
root_cause_non_detection.validation_method, and at least 3 why entries must be filled.""",

    # ── D5 ────────────────────────────────────────────────────────────────────
    "corrective_occurrence": """{
  "corrective_actions_occurrence": [
    {
      "action":      "<string — corrective action description>",
      "responsible": "<string — responsible person name>",
      "due_date":    "<string — ISO date YYYY-MM-DD>"
    }
  ]
}
REQUIRED: at least 1 action with action, responsible, and due_date filled.
Collect ALL actions the user mentions before extracting.""",

    "corrective_detection": """{
  "corrective_actions_detection": [
    {
      "action":      "<string — detection improvement action description>",
      "responsible": "<string — responsible person name>",
      "due_date":    "<string — ISO date YYYY-MM-DD>"
    }
  ]
}
REQUIRED: at least 1 action with action, responsible, and due_date filled.""",

    # ── D6 ────────────────────────────────────────────────────────────────────
    "implementation": """{
  "corrective_actions_occurrence": [
    {
      "action":      "<string — carry over from D5, do not invent>",
      "responsible": "<string — carry over from D5>",
      "due_date":    "<string — carry over from D5>",
      "imp_date":    "<string — actual implementation date YYYY-MM-DD>",
      "evidence":    "<string — file reference or document name>"
    }
  ],
  "corrective_actions_detection": [
    {
      "action":      "<string>",
      "responsible": "<string>",
      "due_date":    "<string>",
      "imp_date":    "<string — actual implementation date YYYY-MM-DD>",
      "evidence":    "<string>"
    }
  ]
}
REQUIRED: at least 1 action in each list must have imp_date filled.
Do NOT invent action descriptions — only add imp_date and evidence for actions the user confirms.""",

    "monitoring_checklist": """{
  "monitoring": {
    "monitoring_interval": "<string — e.g. 3 shifts, 1 week>",
    "pieces_produced":     <number | null>,
    "rejection_rate":      <number | null — percentage>,
    "shift_1_data":        "<string>",
    "shift_2_data":        "<string>"
  },
  "audited_by":  "<string — auditor name and title>",
  "audit_date":  "<string — ISO date YYYY-MM-DD>",
  "num_shifts":  <number — 1, 2, or 3>
}
REQUIRED: monitoring_interval and audited_by must be filled.
num_shifts defaults to 3 if not mentioned.
The checklist tick values are managed manually by the user — do not include checklist in the extraction.""",

    # ── D7 ────────────────────────────────────────────────────────────────────
    "prevention": """{
  "recurrence_risks": [
    {
      "area_line_product":   "<string>",
      "similar_risk_present": "<string — yes | no | unknown>",
      "action_taken":        "<string>"
    }
  ],
  "replication_validations": [
    {
      "line_site":           "<string>",
      "action_replicated":   "<string>",
      "confirmation_method": "<string>",
      "confirmed_by":        "<string>"
    }
  ]
}
REQUIRED: at least 1 recurrence_risks entry with area_line_product and action_taken filled.
Only include entries the user explicitly mentioned.""",

    "knowledge": """{
  "knowledge_base_updates": [
    {
      "document_type":   "<string — e.g. Control Plan, PFMEA, Work Instruction>",
      "topic_reference": "<string>",
      "owner":           "<string>",
      "location_link":   "<string>"
    }
  ],
  "long_term_monitoring": [
    {
      "checkpoint_type": "<string>",
      "frequency":       "<string>",
      "owner":           "<string>",
      "start_date":      "<string — ISO date YYYY-MM-DD>",
      "notes":           "<string>"
    }
  ]
}
REQUIRED: at least 1 knowledge_base_updates entry with document_type filled,
or at least 1 long_term_monitoring entry with checkpoint_type filled.""",

    "lessons_learned": """{
  "lesson_disseminations": [
    {
      "audience_team": "<string>",
      "method":        "<string>",
      "date":          "<string — ISO date YYYY-MM-DD>",
      "owner":         "<string>",
      "evidence":      "<string>"
    }
  ],
  "ll_conclusion": "<string — comprehensive lessons learned summary>"
}
REQUIRED: ll_conclusion must be filled (at least 1 sentence).
At least 1 lesson_disseminations entry with audience_team filled.""",

    # ── D8 ────────────────────────────────────────────────────────────────────
    "closure": """{
  "closure_statement": "<string — comprehensive closure statement, minimum 200 characters>",
  "signatures": {
    "closed_by":     "<string — name and title>",
    "closure_date":  "<string — ISO date YYYY-MM-DD>",
    "approved_by":   "<string — Quality Manager name, or empty>",
    "approval_date": "<string — ISO date YYYY-MM-DD, or empty>"
  }
}
REQUIRED: closure_statement (≥200 chars), signatures.closed_by, signatures.closure_date.
NEVER invent signatures — only extract what the user explicitly provides.""",
}

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

CONV_SYSTEM_PROMPT = """You are a friendly but rigorous 8D Quality Coaching Assistant.
Your job is to help a quality engineer fill in a specific section of an 8D report
through a natural conversation.

RULES:
1. Ask ONE focused follow-up question at a time when information is missing.
2. Be concise: max 3 short sentences per bot message.
3. When the user provides information, acknowledge it briefly and ask for the next missing piece.
4. When you have ALL required fields, output a JSON block using EXACTLY this wrapper:
   <extracted_fields>
   { ... }
   </extracted_fields>
   Then add: "✅ Section complete! All data has been saved. You can now move to the next section."
5. If an answer is too vague, ask for clarification with a concrete example.
6. Never request optional fields before all required fields are collected.
7. Reply in the same language the user writes in (French or English).
8. NEVER invent data — only use what the user explicitly provides.
9. NEVER use field names other than what the schema specifies. For team_members,
   ALWAYS use "department" and "function" — NEVER "role", "job_title", "position", etc.
10. For team_members: keep accumulating members across turns. Re-emit the FULL
    updated list every time you extract (not just the newly added member).
11. If the user mentions uploaded files (shown as "📎 Uploaded: filename.pdf"),
    reference those filenames in evidence_documents field.
"""


# ─────────────────────────────────────────────────────────────────────────────
# MERGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_ALIASES: Dict[str, str] = {
    "role":            "function",
    "job_title":       "function",
    "position":        "function",
    "title":           "function",
    "dept":            "department",
    "department_name": "department",
}

_DEPARTMENT_MAP: Dict[str, str] = {
    "production":        "production",
    "manufacturing":     "production",
    "fabrication":       "production",
    "maintenance":       "maintenance",
    "engineering":       "engineering",
    "r&d":               "engineering",
    "quality":           "quality",
    "qa":                "quality",
    "qc":                "quality",
    "quality control":   "quality",
    "quality assurance": "quality",
    "logistics":         "logistics",
    "supply chain":      "logistics",
    "warehouse":         "logistics",
    "supplier quality":  "supplier_quality",
    "supplier_quality":  "supplier_quality",
    "sqe":               "supplier_quality",
}

_FUNCTION_MAP: Dict[str, str] = {
    "operator":        "operator",
    "line_leader":     "line_leader",
    "line leader":     "line_leader",
    "team leader":     "team_leader",
    "team_leader":     "team_leader",
    "teamleader":      "team_leader",
    "leader":          "team_leader",
    "supervisor":      "supervisor",
    "engineer":        "engineer",
    "engineering":     "engineer",
    "technician":      "engineer",
    "project manager": "project_manager",
    "project_manager": "project_manager",
    "manager":         "project_manager",
}

VALID_DEPARTMENTS = {
    "production", "maintenance", "engineering",
    "quality", "logistics", "supplier_quality", "other",
}
VALID_FUNCTIONS = {
    "operator", "line_leader", "supervisor", "engineer",
    "team_leader", "project_manager", "other",
}


def _normalise_member(raw: Dict) -> Dict:
    member: Dict[str, str] = {}
    for key, value in raw.items():
        canonical_key = _FIELD_ALIASES.get(key.lower(), key.lower())
        member[canonical_key] = str(value).strip() if value else ""

    dept_raw = member.get("department", "").lower().strip()
    member["department"] = _DEPARTMENT_MAP.get(
        dept_raw, dept_raw if dept_raw in VALID_DEPARTMENTS else "other"
    )

    func_raw = member.get("function", "").lower().strip()
    member["function"] = _FUNCTION_MAP.get(
        func_raw, func_raw if func_raw in VALID_FUNCTIONS else "other"
    )

    return {
        "name":       member.get("name", ""),
        "department": member.get("department", "other"),
        "function":   member.get("function", "other"),
    }


def _merge_extracted(current: Dict, extracted: Dict) -> Dict:
    merged = {**current}

    for key, value in extracted.items():
        if key == "team_members" and isinstance(value, list):
            merged["team_members"] = [
                _normalise_member(m) for m in value if isinstance(m, dict)
            ]

        elif key == "is_is_not_factors" and isinstance(value, list):
            existing = {f["factor"]: f for f in (merged.get("is_is_not_factors") or [])}
            for patch in value:
                factor = patch.get("factor")
                if factor:
                    existing[factor] = {**(existing.get(factor) or {}), **patch}
            merged["is_is_not_factors"] = list(existing.values())

        elif key == "five_w_2h" and isinstance(value, dict):
            merged["five_w_2h"] = {**(merged.get("five_w_2h") or {}), **value}

        elif key == "suspected_parts_status" and isinstance(value, list):
            # Merge by location key — preserve existing rows not mentioned
            existing = {r["location"]: r for r in (merged.get("suspected_parts_status") or [])}
            for row in value:
                loc = row.get("location")
                if loc:
                    existing[loc] = {**(existing.get(loc) or {"location": loc}), **row}
            merged["suspected_parts_status"] = list(existing.values())

        elif key in ("four_m_occurrence", "four_m_non_detection") and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}

        elif key in ("five_whys_occurrence", "five_whys_non_detection") and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}

        elif key in ("corrective_actions_occurrence", "corrective_actions_detection") and isinstance(value, list):
            # Replace entirely — D5/D6 sends full list
            merged[key] = value

        elif key == "monitoring" and isinstance(value, dict):
            merged["monitoring"] = {**(merged.get("monitoring") or {}), **value}

        elif key in ("recurrence_risks", "replication_validations",
                     "knowledge_base_updates", "long_term_monitoring",
                     "lesson_disseminations") and isinstance(value, list):
            merged[key] = value

        else:
            merged[key] = value

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# SECTION COMPLETENESS CHECK
# ─────────────────────────────────────────────────────────────────────────────

def _section_is_complete(section_key: str, extracted: Dict) -> bool:
    """
    Return True only when the extracted payload satisfies the minimum
    requirements for the section.
    """
    # ── D1 ────────────────────────────────────────────────────────────────────
    if section_key == "team_members":
        members = extracted.get("team_members", [])
        if not isinstance(members, list) or len(members) < 2:
            return False
        return any(m.get("function") == "team_leader" for m in members if isinstance(m, dict))

    # ── D2 ────────────────────────────────────────────────────────────────────
    if section_key == "five_w_2h":
        w2h = extracted.get("five_w_2h", {})
        if not isinstance(w2h, dict):
            return False
        return all(str(w2h.get(k, "")).strip() for k in ("what", "where", "when", "who", "why", "how", "how_many"))

    if section_key == "deviation":
        return all(
            str(extracted.get(k, "")).strip()
            for k in ("standard_applicable", "expected_situation", "observed_situation")
        )

    if section_key == "is_is_not":
        factors = extracted.get("is_is_not_factors", [])
        if not isinstance(factors, list):
            return False
        filled = sum(
            1 for f in factors
            if isinstance(f, dict)
            and str(f.get("is_problem", "")).strip()
            and str(f.get("is_not_problem", "")).strip()
        )
        return filled >= 3

    # ── D3 ────────────────────────────────────────────────────────────────────
    if section_key == "containment":
        dps = extracted.get("defected_part_status", {})
        has_defected = isinstance(dps, dict) and any(
            v for k, v in dps.items() if k in ("returned", "isolated", "identified")
        )
        suspected = extracted.get("suspected_parts_status", [])
        has_suspected = isinstance(suspected, list) and any(
            str(r.get("actions", "")).strip() for r in suspected if isinstance(r, dict)
        )
        alert_to = extracted.get("alert_communicated_to", {})
        has_alert = (
            isinstance(alert_to, dict) and any(alert_to.values())
        ) or str(extracted.get("alert_number", "")).strip()
        return (has_defected or has_suspected) and has_alert

    if section_key == "restart":
        rp = extracted.get("restart_production", {})
        return (
            isinstance(rp, dict)
            and str(rp.get("when", "")).strip()
            and str(rp.get("approved_by", "")).strip()
            and str(extracted.get("containment_responsible", "")).strip()
        )

    # ── D4 ────────────────────────────────────────────────────────────────────
    if section_key == "four_m_occurrence":
        fm = extracted.get("four_m_occurrence", {})
        rc = extracted.get("root_cause_occurrence", {})
        whys = extracted.get("five_whys_occurrence", {})
        whys_filled = sum(
            1 for w in whys.values()
            if isinstance(w, dict) and str(w.get("answer", "")).strip()
        ) if isinstance(whys, dict) else 0
        return (
            isinstance(fm, dict) and str(fm.get("selected_problem", "")).strip()
            and isinstance(rc, dict) and str(rc.get("root_cause", "")).strip()
            and str(rc.get("validation_method", "")).strip()
            and whys_filled >= 3
        )

    if section_key == "four_m_non_detection":
        fm = extracted.get("four_m_non_detection", {})
        rc = extracted.get("root_cause_non_detection", {})
        whys = extracted.get("five_whys_non_detection", {})
        whys_filled = sum(
            1 for w in whys.values()
            if isinstance(w, dict) and str(w.get("answer", "")).strip()
        ) if isinstance(whys, dict) else 0
        return (
            isinstance(fm, dict) and str(fm.get("selected_problem", "")).strip()
            and isinstance(rc, dict) and str(rc.get("root_cause", "")).strip()
            and str(rc.get("validation_method", "")).strip()
            and whys_filled >= 3
        )

    # ── D5 ────────────────────────────────────────────────────────────────────
    if section_key == "corrective_occurrence":
        actions = extracted.get("corrective_actions_occurrence", [])
        return isinstance(actions, list) and any(
            str(a.get("action", "")).strip() and str(a.get("responsible", "")).strip()
            for a in actions if isinstance(a, dict)
        )

    if section_key == "corrective_detection":
        actions = extracted.get("corrective_actions_detection", [])
        return isinstance(actions, list) and any(
            str(a.get("action", "")).strip() and str(a.get("responsible", "")).strip()
            for a in actions if isinstance(a, dict)
        )

    # ── D6 ────────────────────────────────────────────────────────────────────
    if section_key == "implementation":
        occ = extracted.get("corrective_actions_occurrence", [])
        det = extracted.get("corrective_actions_detection", [])
        has_occ = isinstance(occ, list) and any(str(a.get("imp_date", "")).strip() for a in occ if isinstance(a, dict))
        has_det = isinstance(det, list) and any(str(a.get("imp_date", "")).strip() for a in det if isinstance(a, dict))
        return has_occ or has_det

    if section_key == "monitoring_checklist":
        mon = extracted.get("monitoring", {})
        return (
            isinstance(mon, dict) and str(mon.get("monitoring_interval", "")).strip()
            and str(extracted.get("audited_by", "")).strip()
        )

    # ── D7 ────────────────────────────────────────────────────────────────────
    if section_key == "prevention":
        risks = extracted.get("recurrence_risks", [])
        return isinstance(risks, list) and any(
            str(r.get("area_line_product", "")).strip() and str(r.get("action_taken", "")).strip()
            for r in risks if isinstance(r, dict)
        )

    if section_key == "knowledge":
        kb = extracted.get("knowledge_base_updates", [])
        ltm = extracted.get("long_term_monitoring", [])
        has_kb = isinstance(kb, list) and any(str(u.get("document_type", "")).strip() for u in kb if isinstance(u, dict))
        has_ltm = isinstance(ltm, list) and any(str(m.get("checkpoint_type", "")).strip() for m in ltm if isinstance(m, dict))
        return has_kb or has_ltm

    if section_key == "lessons_learned":
        disem = extracted.get("lesson_disseminations", [])
        has_dissem = isinstance(disem, list) and any(str(d.get("audience_team", "")).strip() for d in disem if isinstance(d, dict))
        has_conclusion = str(extracted.get("ll_conclusion", "")).strip()
        return has_dissem and bool(has_conclusion)

    # ── D8 ────────────────────────────────────────────────────────────────────
    if section_key == "closure":
        statement = str(extracted.get("closure_statement", "")).strip()
        sigs = extracted.get("signatures", {})
        return (
            len(statement) >= 200
            and isinstance(sigs, dict)
            and str(sigs.get("closed_by", "")).strip()
            and str(sigs.get("closure_date", "")).strip()
        )

    # Unknown section — treat any extraction as completion signal
    return bool(extracted)


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE
# ─────────────────────────────────────────────────────────────────────────────

class ConversationService:
    def __init__(self, db: Session):
        self.db = db
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_or_start_conversation(
        self,
        step_id: int,
        section_key: str,
    ) -> Dict[str, Any]:
        """Return existing messages for a section, or start fresh."""
        messages = self._load_messages(step_id, section_key)
        if not messages:
            opening = SECTION_OPENING.get(
                section_key,
                "Let's fill in this section. Please provide the required information.",
            )
            self._persist_message(step_id, section_key, "assistant", opening, 0)
            messages = [self._msg_dict("assistant", opening, 0)]

        return {
            "step_id":     step_id,
            "section_key": section_key,
            "messages":    messages,
            "state":       self._infer_state(section_key, messages),
        }

    def send_message(
        self,
        step_id: int,
        section_key: str,
        user_message: str,
        complaint_context: Optional[Dict] = None,
        uploaded_file_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Process a user message: persist → call AI → persist reply → return.

        Extracted fields are immediately merged into step.data in the DB.
        When all sections for the step are complete, step.status is set to 'fulfilled'.
        """
        # Inject uploaded file info into user message text
        effective_message = user_message
        if uploaded_file_names:
            file_list = ", ".join(uploaded_file_names)
            effective_message = (
                f"{user_message}\n\n📎 Uploaded: {file_list}"
                if user_message.strip()
                else f"📎 Uploaded: {file_list}"
            )

        history  = self._load_messages(step_id, section_key)
        next_idx = len(history)

        self._persist_message(step_id, section_key, "user", effective_message, next_idx)
        history.append(self._msg_dict("user", effective_message, next_idx))
        next_idx += 1

        # Inject existing step-file names into context so AI knows what's on record
        existing_files = self._get_step_file_names(step_id)
        bot_reply = self._call_ai(
            section_key, history, complaint_context, existing_files
        )

        extracted = self._parse_extracted_fields(bot_reply)
        meta      = {"extracted_fields": extracted} if extracted else None

        self._persist_message(step_id, section_key, "assistant", bot_reply, next_idx, meta)
        history.append(self._msg_dict("assistant", bot_reply, next_idx, meta))

        if extracted:
            # For deviation section: merge uploaded file names into evidence_documents
            if section_key == "deviation" and existing_files:
                current_evidence = extracted.get("evidence_documents", "")
                all_names = list(existing_files)
                if current_evidence:
                    for name in current_evidence.split(","):
                        name = name.strip()
                        if name and name not in all_names:
                            all_names.append(name)
                extracted["evidence_documents"] = ", ".join(all_names)

            # Persist extracted fields into step.data immediately
            self._update_step_data(step_id, extracted)

        section_complete = bool(extracted and _section_is_complete(section_key, extracted))

        # Determine conversation state for this section
        if section_complete:
            state = "fulfilled"
            # Check if the entire step is now fulfilled and update step.status
            self._maybe_mark_step_fulfilled(step_id, section_key)
        elif len(history) > 1:
            state = "collecting"
        else:
            state = "opening"

        return {
            "step_id":          step_id,
            "section_key":      section_key,
            "bot_reply":        bot_reply,
            "extracted_fields": extracted,
            "state":            state,
            "messages":         history,
        }

    def reset_conversation(self, step_id: int, section_key: str) -> Dict[str, Any]:
        self.db.query(StepConversation).filter(
            StepConversation.report_step_id == step_id,
            StepConversation.section_key    == section_key,
        ).delete()
        self.db.commit()
        return self.get_or_start_conversation(step_id, section_key)

    def get_all_section_conversations(self, step_id: int) -> Dict[str, List[Dict]]:
        rows = (
            self.db.query(StepConversation)
            .filter(StepConversation.report_step_id == step_id)
            .order_by(StepConversation.section_key, StepConversation.message_index)
            .all()
        )
        result: Dict[str, List[Dict]] = {}
        for row in rows:
            result.setdefault(row.section_key, []).append(
                self._msg_dict(row.role, row.content, row.message_index, row.meta)
            )
        return result

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_step_file_names(self, step_id: int) -> List[str]:
        """Return original filenames of all files attached to this step."""
        rows = (
            self.db.query(FileModel.original_name)
            .join(StepFile, StepFile.file_id == FileModel.id)
            .filter(StepFile.report_step_id == step_id)
            .all()
        )
        return [r.original_name for r in rows]

    def _call_ai(
        self,
        section_key: str,
        history: List[Dict],
        complaint_context: Optional[Dict],
        existing_files: Optional[List[str]] = None,
    ) -> str:
        schema = EXTRACTION_SCHEMA.get(section_key, "")
        context_block = ""
        if complaint_context:
            context_block = (
                "\n\nCOMPLAINT CONTEXT (use to assess relevance):\n"
                f"  Problem : {complaint_context.get('complaint_name', '')}\n"
                f"  Desc    : {complaint_context.get('complaint_description', '')}\n"
                f"  Product : {complaint_context.get('product_line', '')}\n"
                f"  Plant   : {complaint_context.get('plant', '')}\n"
            )

        files_block = ""
        if existing_files:
            files_block = (
                "\n\nATTACHED EVIDENCE FILES (already uploaded to this step):\n"
                + "\n".join(f"  - {f}" for f in existing_files)
                + "\nReference these in evidence_documents when extracting.\n"
            )

        system = (
            CONV_SYSTEM_PROMPT
            + f"\n\nSECTION: {section_key}"
            + context_block
            + files_block
            + "\n\nWhen all required fields are collected, wrap JSON in "
              "<extracted_fields>...</extracted_fields>.\n"
            + f"Required JSON schema:\n{schema}"
        )

        openai_messages = [{"role": "system", "content": system}]
        for msg in history:
            openai_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=openai_messages,
                temperature=0.4,
                max_tokens=600,
                timeout=30,
            )
            return response.choices[0].message.content.strip()
        except OpenAIError as e:
            logger.error("OpenAI conversation error: %s", e)
            raise RuntimeError(f"AI service unavailable: {e}")

    def _load_messages(self, step_id: int, section_key: str) -> List[Dict]:
        rows = (
            self.db.query(StepConversation)
            .filter(
                StepConversation.report_step_id == step_id,
                StepConversation.section_key    == section_key,
            )
            .order_by(StepConversation.message_index)
            .all()
        )
        return [
            self._msg_dict(r.role, r.content, r.message_index, r.meta, r.created_at)
            for r in rows
        ]

    def _persist_message(
        self,
        step_id: int,
        section_key: str,
        role: str,
        content: str,
        message_index: int,
        meta: Optional[Dict] = None,
    ) -> None:
        self.db.add(StepConversation(
            report_step_id=step_id,
            section_key=section_key,
            role=role,
            content=content,
            message_index=message_index,
            meta=meta,
            created_at=datetime.now(timezone.utc),
        ))
        self.db.commit()

    @staticmethod
    def _msg_dict(
        role: str,
        content: str,
        message_index: int,
        meta: Optional[Dict]           = None,
        created_at: Optional[datetime] = None,
    ) -> Dict:
        return {
            "role":          role,
            "content":       content,
            "message_index": message_index,
            "meta":          meta,
            "created_at":    created_at.isoformat() if created_at else None,
        }

    @staticmethod
    def _parse_extracted_fields(text: str) -> Optional[Dict]:
        match = re.search(
            r"<extracted_fields>(.*?)</extracted_fields>", text, re.DOTALL
        )
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            logger.warning("Failed to parse extracted_fields JSON from AI reply")
            return None

    def _update_step_data(self, step_id: int, extracted: Dict) -> None:
        """
        Merge extracted fields into step.data and persist immediately.
        This is the single source of truth for saving conversation-collected data.
        """
        from app.models.report_step import ReportStep  # avoid circular import

        step = self.db.get(ReportStep, step_id)
        if step is None:
            logger.warning("_update_step_data: step %d not found", step_id)
            return

        current = step.data or {}
        merged  = _merge_extracted(current, extracted)

        step.data       = merged
        step.updated_at = datetime.now(timezone.utc)
        self.db.commit()

        logger.info(
            "Saved extracted fields to step %d (keys: %s)",
            step_id,
            list(extracted.keys()),
        )

    def _maybe_mark_step_fulfilled(self, step_id: int, just_completed_section: str) -> None:
        """
        After a section completes, check whether ALL sections for this step
        are now fulfilled. If so (or if the step has no multi-section config,
        e.g. D1), mark step.status = 'fulfilled'.
        """
        from app.models.report_step import ReportStep  # avoid circular import

        step = self.db.get(ReportStep, step_id)
        if step is None:
            return
        complaint = step.report.complaint

        step_code    = step.step_code
        all_sections = get_all_section_keys(step_code)

        if not all_sections:
            # D1 or any step with no section config: fulfilled when one section completes
            step.status       = "fulfilled"
            logger.info("Step %d (%s) has no multi-section config — marked fulfilled", step_id, step_code)
            complaint.status=step.step_code
            logger.info("Complaint %d status updated to %s", complaint.id, complaint.status)
            step.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            logger.info("Step %d (%s) marked fulfilled (no multi-section config)", step_id, step_code)
            return

        # Check completion status of every section by scanning conversation history
        for section_key in all_sections:
            if section_key == just_completed_section:
                continue
            if not self._is_section_fulfilled(step_id, section_key):
                logger.debug(
                    "Step %d: section '%s' not yet fulfilled — step stays draft",
                    step_id, section_key,
                )
                return

        # All sections complete → mark fulfilled
        step.status       = "fulfilled"
        complaint.status=step.step_code
        logger.info("Complaint %d status updated to %s", complaint.id, complaint.status)

        step.completed_at = datetime.now(timezone.utc)
        self.db.commit()
        logger.info(
            "Step %d (%s) marked fulfilled — all sections complete",
            step_id, step_code,
        )

    def _is_section_fulfilled(self, step_id: int, section_key: str) -> bool:
        """
        Return True if the conversation for this section contains a completed
        extraction (assistant message whose meta.extracted_fields passes
        _section_is_complete).
        """
        rows = (
            self.db.query(StepConversation)
            .filter(
                StepConversation.report_step_id == step_id,
                StepConversation.section_key    == section_key,
                StepConversation.role           == "assistant",
            )
            .order_by(StepConversation.message_index)
            .all()
        )
        for row in rows:
            meta = row.meta or {}
            extracted = meta.get("extracted_fields")
            if extracted and _section_is_complete(section_key, extracted):
                return True
        return False

    @staticmethod
    def _infer_state(section_key: str, messages: List[Dict]) -> str:
        """
        Infer conversation state from message history for GET requests.
        Returns 'fulfilled', 'collecting', or 'opening'.
        """
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                meta = msg.get("meta") or {}
                extracted = meta.get("extracted_fields")
                if extracted and _section_is_complete(section_key, extracted):
                    return "fulfilled"
        return "opening" if len(messages) <= 1 else "collecting"