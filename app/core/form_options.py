"""
app/core/form_options.py

Controlled vocabularies mirrored from the "New Complaint" frontend form, plus a
completeness checker used to decide whether an email intake can be promoted
directly to a complaint (path B) or needs a human to complete it (path A).

NOTE: CUSTOMERS and PROCESSES are duplicated from the frontend
(src/pages/NewComplaint.tsx). If the form's lists change, update them here too.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from app.models.enums import PlantEnum, ProductLineEnum

# ── Controlled vocabularies (must match the form) ────────────────────────────
CLAIM_TYPES = ["CS2", "CS1", "WR", "Quality Alert"]
DEFECTS = ["Function", "Fit", "Dimensional", "Appearance"]
PROCESSES = [
    "ASSEMBLY", "TESTING", "WINDING", "GLUING", "BAKING", "GRINDING", "PILLING",
    "CRIMPING", "WELDING", "SOLDERING", "INSPECTION", "PLASTIC INJECTION",
    "PLASTIC DEFLASHING", "PRODUCT TREACEABILITY", "SHIPPING", "LAPPING", "TAMPING",
]
CUSTOMERS = [
    "VALEO", "INTEVA", "DENSO", "KELI", "NIDEC", "MAHLE", "HELLA", "HAYWARD",
    "ADVIK", "DOLZ", "BOSCH", "E-MOTOR", "BMW", "VW", "PHINIA", "BOSCH POWERTOOL",
    "STANLEY - BLACK AND DECKER", "RUIDONG", "BYD", "KOSTAL", "RENAULT", "MIMZHEN",
    "BORGWANER", "TESLA", "US MOTOR WORKS", "AUDI", "SPECK", "PIERBURG", "BUEHLER",
    "CEBI", "YAMAHA", "ELEKTRA", "RÖMER", "EUROTEC", "Tianjin Yixin", "Tiang Long",
    "Rui Wei", "Li Shui Qiangrun", "Ji Ou", "Lang Xin", "Jiang Su Yun Tai",
    "Kinetic", "Zhuo Ren", "Dong Jiang", "Wu Zhou Ren Xin", "Guangzhou Hua Wang",
    "Xuan Pu", "Fine-World",
]

PRODUCT_LINES = [e.value for e in ProductLineEnum]
PLANTS = [e.value for e in PlantEnum]

# Free-text fields that just need to be non-empty
_REQUIRED_TEXT = [
    "complaint_name",
    "customer_plant_name",
    "avocarbon_product_type",
    "complaint_description",
]


def _match(value: Any, options: list[str]) -> Optional[str]:
    """Case-insensitive, trimmed match; returns the canonical option or None."""
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    low = v.lower()
    for opt in options:
        if opt.lower() == low:
            return opt
    return None


def _parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "")).date()
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def evaluate_completeness(extracted: dict) -> tuple[dict, list[str]]:
    """
    Check an intake's extracted_data against the New Complaint form's required
    fields and controlled vocabularies.

    Returns (normalized, missing):
      normalized : cleaned/canonical values for the fields that ARE valid
      missing    : names of required fields that are missing or invalid

    Fields handled elsewhere (cqt_email, QM/PM emails, complaint_opening_date,
    repetitive_complete_with_number) are NOT checked here.
    """
    extracted = extracted or {}
    normalized: dict = {}
    missing: list[str] = []

    # ── Controlled-vocabulary fields ─────────────────────────────────────────
    controlled = [
        ("quality_issue_warranty", CLAIM_TYPES),
        ("product_line", PRODUCT_LINES),
        ("avocarbon_plant", PLANTS),
        ("potential_avocarbon_process_linked_to_problem", PROCESSES),
        ("defects", DEFECTS),
        ("customer", CUSTOMERS),
    ]
    for field, options in controlled:
        canonical = _match(extracted.get(field), options)
        if canonical is None:
            missing.append(field)
        else:
            normalized[field] = canonical

    # ── Required free-text fields ────────────────────────────────────────────
    for field in _REQUIRED_TEXT:
        val = extracted.get(field)
        if val is None or not str(val).strip():
            missing.append(field)
        else:
            normalized[field] = str(val).strip()

    # ── Required date ────────────────────────────────────────────────────────
    d = _parse_date(extracted.get("customer_complaint_date"))
    if d is None:
        missing.append("customer_complaint_date")
    else:
        normalized["customer_complaint_date"] = d.isoformat()

    # ── Optional passthroughs (not required, kept if present) ────────────────
    if extracted.get("concerned_application"):
        normalized["concerned_application"] = str(extracted["concerned_application"]).strip()
    if extracted.get("repetitive_complete_with_number") not in (None, ""):
        normalized["repetitive_complete_with_number"] = str(
            extracted["repetitive_complete_with_number"]
        ).strip()

    return normalized, missing
