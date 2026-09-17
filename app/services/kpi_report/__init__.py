"""
app/services/reports/
─────────────────────
Three-layer report package:

  kpi_logic.py      — data extraction, aggregation, targets  (backend engineer)
  ai_summary.py     — OpenAI calls, prompt building, AI panel  (backend engineer)
  report_design.py  — palette, charts, tables, layout, doc template  (design collaborator)
  report_builder.py — orchestration: calls all three layers  (shared)

Public API (mirrors the original kpi_report_pdf.py):
"""

from .report_builder import (
    consolidated_report,
    per_plant_report,
    process_pilot_report,
)

__all__ = ["per_plant_report", "consolidated_report", "process_pilot_report"]