# app/services/section_config.py
"""
Section configuration for per-section AI validation.

Each D step is split into named sections. Each section defines:
  - fields: the data keys extracted from step.data and sent to the AI
  - label:  human-readable name used in coaching KB section_hint lookup

KB section_hint convention:
    D{N}_{SECTION_KEY}_coaching_validation
    e.g.  D2_five_w_2h_coaching_validation
          D4_four_m_occurrence_coaching_validation
"""

from typing import Dict, List

# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------
SectionDef = Dict[str, List[str]]   # section_key → list of data fields


# ---------------------------------------------------------------------------
# D2 — Describe the Problem
# ---------------------------------------------------------------------------
D2_SECTIONS: SectionDef = {
    "five_w_2h": [
        "problem_description",
        "five_w_2h",
    ],
    "deviation": [
        "standard_applicable",
        "expected_situation",
        "observed_situation",
        "evidence_documents",
    ],
    "is_is_not": [
        "is_is_not_factors",
    ],
}

# ---------------------------------------------------------------------------
# D3 — Interim Containment
# ---------------------------------------------------------------------------
D3_SECTIONS: SectionDef = {
    "defected_parts":   ["defected_part_status"],
    "suspected_parts":  ["suspected_parts_status", "alert_communicated_to", "alert_number"],
    "restart":          ["restart_production", "containment_responsible"],
}

# ---------------------------------------------------------------------------
# D4 — Root Cause Analysis
# ---------------------------------------------------------------------------
D4_SECTIONS: SectionDef = {
    "four_m_occurrence": [
        "four_m_occurrence",
        "five_whys_occurrence",
        "root_cause_occurrence",
    ],
    "four_m_non_detection": [
        "four_m_non_detection",
        "five_whys_non_detection",
        "root_cause_non_detection",
    ],
}

# ---------------------------------------------------------------------------
# D5 — Corrective Actions (planning)
# ---------------------------------------------------------------------------
D5_SECTIONS: SectionDef = {
    "corrective_occurrence": [
        "corrective_actions_occurrence",
    ],
    "corrective_detection": [
        "corrective_actions_detection",
    ],
}

# ---------------------------------------------------------------------------
# D6 — Implementation & Effectiveness
#
# Frontend section keys (2 tabs):
#   "implementation"        → tab 1
#   "monitoring_checklist"  → tab 2  (monitoring + checklist merged)
# ---------------------------------------------------------------------------
D6_SECTIONS: SectionDef = {
    "implementation": [
        "corrective_actions_occurrence",
        "corrective_actions_detection",
    ],
    "monitoring_checklist": [
        "monitoring",
        "checklist",
        "audited_by",
        "audit_date",
        "num_shifts",
    ],
}

# ---------------------------------------------------------------------------
# D7 — Prevent Recurrence
#
# Frontend section keys (3 tabs):
#   "prevention"      → tab 1: recurrence risks + replication validation
#   "knowledge"       → tab 2: knowledge base updates + long-term monitoring
#   "lessons_learned" → tab 3: dissemination + LL conclusion
# ---------------------------------------------------------------------------
D7_SECTIONS: SectionDef = {
    "prevention": [
        "recurrence_risks",
        "replication_validations",
    ],
    "knowledge": [
        "knowledge_base_updates",
        "long_term_monitoring",
    ],
    "lessons_learned": [
        "lesson_disseminations",
        "ll_conclusion",
    ],
}

# ---------------------------------------------------------------------------
# D8 — Closure
# ---------------------------------------------------------------------------
D8_SECTIONS: SectionDef = {
    "closure": [
        "closure_statement",
        "signatures",
    ],
}

# ---------------------------------------------------------------------------
# Master registry — used by StepService
# ---------------------------------------------------------------------------
STEP_SECTIONS: Dict[str, SectionDef] = {
    "D2": D2_SECTIONS,
    "D3": D3_SECTIONS,
    "D4": D4_SECTIONS,
    "D5": D5_SECTIONS,
    "D6": D6_SECTIONS,
    "D7": D7_SECTIONS,
    "D8": D8_SECTIONS,
    # D1 uses local validation only — no sections needed
}


def get_section_fields(step_code: str, section_key: str) -> List[str]:
    """Return the list of data fields for a given step+section."""
    step_def = STEP_SECTIONS.get(step_code)
    if not step_def:
        raise ValueError(f"No section config for step '{step_code}'")
    fields = step_def.get(section_key)
    if fields is None:
        raise ValueError(f"Unknown section '{section_key}' for step '{step_code}'")
    return fields


def get_all_section_keys(step_code: str) -> List[str]:
    """Return all section keys for a step."""
    step_def = STEP_SECTIONS.get(step_code)
    if not step_def:
        return []
    return list(step_def.keys())


