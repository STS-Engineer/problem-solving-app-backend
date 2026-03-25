"""
app/api/endpoints/audit_priorities.py
──────────────────────────────────────────────────────────
Pull endpoint consumed by the audit app

GET /api/v1/complaints/audit-priorities?month=YYYY-MM&window_days=30

Priority rules (deterministic, mutually exclusive per complaint/group):
  P1 — open complaint past its due_date
  P2 — open CS2 complaint
  P3 — open CS1 complaint
  P4 — customer with > 3 complaints in the last 30 days
  P5 — complaint with repetition_count > 0 in the last 30 days

A single complaint can appear in multiple sections if it satisfies several
rules (e.g. an overdue CS2 appears in both P1 and P2). The audit app
planner decides which AuditCandidate to create — this endpoint just surfaces
the raw signals ranked by severity.

"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.complaint import Complaint

router = APIRouter()

_OPEN_STATUSES_EXCLUDE = {"closed", "resolved", "rejected"}


def _is_open(complaint: Complaint) -> bool:
    return complaint.status not in _OPEN_STATUSES_EXCLUDE


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _complaint_summary(c: Complaint) -> dict[str, Any]:
    return {
        "id": c.id,
        "reference_number": c.reference_number,
        "quality_issue_warranty": c.quality_issue_warranty,
        "customer": c.customer,
        "avocarbon_plant": c.avocarbon_plant.value if c.avocarbon_plant else None,
        "product_line": c.product_line.value if c.product_line else None,
        "defects": c.defects,
        "status": c.status,
        "priority": c.priority,
        "due_date": c.due_date.isoformat() if c.due_date else None,
        "complaint_opening_date": (
            c.complaint_opening_date.isoformat() if c.complaint_opening_date else None
        ),
        "repetition_count": _safe_int(c.repetitive_complete_with_number),
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get(
    "/audit-priorities",
    summary="Audit priority signals for the planner (pull)",
    response_model=dict,
)
def get_audit_priorities(
    month: str = Query(
        ...,
        description="Target planning month YYYY-MM",
        example="2026-05",
        regex=r"^\d{4}-\d{2}$",
    ),
    window_days: int = Query(
        30,
        description="Look-back window in days for P4/P5 analysis",
        ge=7,
        le=90,
    ),
    customer_threshold: int = Query(
        3,
        description="Complaint count above which a customer triggers P4",
        ge=1,
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Returns five priority sections. The audit planner iterates them in order
    (P1 first) and creates AuditCandidate rows as needed.

    Each item includes enough context for the planner to populate:
      AuditCandidate.priority_code, .candidate_type, .plant,
      .product_line, .process_name, .target_month, .primary_complaint_id
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=window_days)

    # ── Fetch all open complaints (P1/P2/P3) ─────────────────────────────────
    open_complaints: list[Complaint] = (
        db.query(Complaint)
        .filter(Complaint.status.notin_(_OPEN_STATUSES_EXCLUDE))
        .order_by(Complaint.due_date.asc().nullslast())
        .all()
    )

    # ── Fetch all complaints in window (P4/P5) ────────────────────────────────
    window_complaints: list[Complaint] = (
        db.query(Complaint)
        .filter(Complaint.created_at >= since)
        .order_by(Complaint.created_at.desc())
        .all()
    )

    # ─────────────────────────────────────────────────────────────────────────
    # P1 — overdue open complaints
    # ─────────────────────────────────────────────────────────────────────────
    p1_items = []
    for c in open_complaints:
        if not c.due_date:
            continue
        due = (
            c.due_date if c.due_date.tzinfo else c.due_date.replace(tzinfo=timezone.utc)
        )
        if due >= now:
            continue

        days_overdue = (now - due).days
        p1_items.append(
            {
                "priority_code": "P1",
                "candidate_type": (
                    "8D"
                    if (c.quality_issue_warranty or "").strip() == "CS2"
                    else "CS1_CHECKLIST"
                ),
                "target_month": month,
                "plant": c.avocarbon_plant.value if c.avocarbon_plant else None,
                "product_line": c.product_line.value if c.product_line else None,
                "primary_complaint_id": c.id,
                "reason": (
                    f"{c.reference_number} is {days_overdue} day(s) overdue "
                    f"(type={c.quality_issue_warranty}, status={c.status})"
                ),
                "days_overdue": days_overdue,
                "complaint": _complaint_summary(c),
            }
        )

    # Sort: most overdue first
    p1_items.sort(key=lambda x: x["days_overdue"], reverse=True)

    # ─────────────────────────────────────────────────────────────────────────
    # P2 — open CS2 complaints
    # ─────────────────────────────────────────────────────────────────────────
    p2_items = []
    for c in open_complaints:
        if (c.quality_issue_warranty or "").strip() != "CS2":
            continue
        p2_items.append(
            {
                "priority_code": "P2",
                "candidate_type": "8D",
                "target_month": month,
                "plant": c.avocarbon_plant.value if c.avocarbon_plant else None,
                "product_line": c.product_line.value if c.product_line else None,
                "primary_complaint_id": c.id,
                "reason": f"{c.reference_number} is an open CS2 complaint",
                "complaint": _complaint_summary(c),
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    # P3 — open CS1 complaints
    # ─────────────────────────────────────────────────────────────────────────
    p3_items = []
    for c in open_complaints:
        if (c.quality_issue_warranty or "").strip() != "CS1":
            continue
        p3_items.append(
            {
                "priority_code": "P3",
                "candidate_type": "CS1_CHECKLIST",
                "target_month": month,
                "plant": c.avocarbon_plant.value if c.avocarbon_plant else None,
                "product_line": c.product_line.value if c.product_line else None,
                "primary_complaint_id": c.id,
                "reason": f"{c.reference_number} is an open CS1 complaint",
                "complaint": _complaint_summary(c),
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    # P4 — customers with > threshold complaints in the window
    # Group by: customer
    # You can extend the grouping key below (e.g. add avocarbon_plant) if needed
    # ─────────────────────────────────────────────────────────────────────────
    by_customer: dict[str, list[Complaint]] = defaultdict(list)
    for c in window_complaints:
        if c.customer:
            by_customer[c.customer].append(c)

    p4_items = []
    for customer, group in by_customer.items():
        if len(group) <= customer_threshold:
            continue

        # Pick a representative plant/product_line (most common in the group)
        plants = [c.avocarbon_plant.value for c in group if c.avocarbon_plant]
        products = [c.product_line.value for c in group if c.product_line]
        plant = max(set(plants), key=plants.count) if plants else None
        product_line = max(set(products), key=products.count) if products else None

        p4_items.append(
            {
                "priority_code": "P4",
                "candidate_type": "CS2_CHECKLIST",
                "target_month": month,
                "plant": plant,
                "product_line": product_line,
                "process_name": None,
                "primary_complaint_id": group[0].id,
                "reason": (
                    f"Customer '{customer}' has {len(group)} complaint(s) "
                    f"in the last {window_days} days (threshold: >{customer_threshold})"
                ),
                "customer": customer,
                "complaint_count": len(group),
                "complaints": [_complaint_summary(c) for c in group],
            }
        )

    # Sort: most complaints first
    p4_items.sort(key=lambda x: x["complaint_count"], reverse=True)

    # ─────────────────────────────────────────────────────────────────────────
    # P5 — repetitive complaints in the window (repetition_count > 0)
    # Group by: (avocarbon_plant, product_line, defects)
    # ─────────────────────────────────────────────────────────────────────────
    by_pattern: dict[tuple, list[Complaint]] = defaultdict(list)
    for c in window_complaints:
        rep = _safe_int(c.repetitive_complete_with_number)
        if rep == 0:
            continue  # first-time complaint — not repetitive
        if not c.defects:
            continue
        key = (
            c.avocarbon_plant.value if c.avocarbon_plant else None,
            c.product_line.value if c.product_line else None,
            c.defects,
        )
        by_pattern[key].append(c)

    p5_items = []
    for (plant, product_line, defect), group in by_pattern.items():
        max_rep = max(_safe_int(c.repetitive_complete_with_number) for c in group)
        total_occurrences = sum(
            _safe_int(c.repetitive_complete_with_number) + 1 for c in group
        )

        p5_items.append(
            {
                "priority_code": "P5",
                "candidate_type": "PROCESS",
                "target_month": month,
                "plant": plant,
                "product_line": product_line,
                "process_name": defect,
                "primary_complaint_id": group[0].id,
                "reason": (
                    f"Repetitive defect '{defect}' on {plant}/{product_line}: "
                    f"{len(group)} complaint(s) with max repetition_count={max_rep}"
                ),
                "max_repetition_count": max_rep,
                "total_occurrences": total_occurrences,
                "complaint_count": len(group),
                "complaints": [
                    _complaint_summary(c)
                    for c in sorted(
                        group,
                        key=lambda c: _safe_int(c.repetitive_complete_with_number),
                        reverse=True,
                    )
                ],
            }
        )

    # Sort: highest repetition count first
    p5_items.sort(key=lambda x: x["max_repetition_count"], reverse=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Response
    # ─────────────────────────────────────────────────────────────────────────
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_month": month,
        "window_days": window_days,
        "summary": {
            "P1_overdue": len(p1_items),
            "P2_open_cs2": len(p2_items),
            "P3_open_cs1": len(p3_items),
            "P4_customer_surge": len(p4_items),
            "P5_repetitive_patterns": len(p5_items),
            "total_signals": len(p1_items)
            + len(p2_items)
            + len(p3_items)
            + len(p4_items)
            + len(p5_items),
        },
        "P1_overdue_complaints": p1_items,
        "P2_open_cs2": p2_items,
        "P3_open_cs1": p3_items,
        "P4_customer_surges": p4_items,
        "P5_repetitive_patterns": p5_items,
    }
