"""
app/services/reports/ai_summary.py
────────────────────────────────────
Owner: KPI / backend engineer
Responsibility: All OpenAI interaction — prompt building, API calls, response
parsing, and assembling the AI panel flowable for ReportLab.
No raw data logic lives here (use kpi_logic.py for that).
"""

from __future__ import annotations

import json
import logging
import os
import re
import textwrap
from datetime import date
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .kpi_logic import MONTH_LONG, MONTH_SHORT

logger = logging.getLogger(__name__)

# ── Colour references (minimal — only what the AI panel needs) ───────────────
# Imported lazily from report_design to avoid a hard circular dependency;
# if you prefer, duplicate the handful of hex values here instead.
try:
    from .report_design import (
        C_AMBER, C_COBALT, C_ELECTRIC, C_FROST, C_GREEN, C_ICE,
        C_INK, C_MIST, C_NAVY, C_PAPER, C_RED, C_SLATE, C_WHITE,
        _AI_COL_W, _BODY_W, _CHART_COL_W,
    )
except ImportError:
    # Fallback hard-coded values so this module can be unit-tested standalone
    from reportlab.lib import colors as _c
    C_NAVY     = _c.HexColor("#0D1B2A")
    C_COBALT   = _c.HexColor("#1C4E80")
    C_ELECTRIC = _c.HexColor("#0EA5E9")
    C_ICE      = _c.HexColor("#BAE6FD")
    C_FROST    = _c.HexColor("#F0F9FF")
    C_PAPER    = _c.HexColor("#F8FAFC")
    C_WHITE    = _c.white
    C_INK      = _c.HexColor("#0A0F1E")
    C_SLATE    = _c.HexColor("#475569")
    C_MIST     = _c.HexColor("#CBD5E1")
    C_RED      = _c.HexColor("#EF4444")
    C_AMBER    = _c.HexColor("#F59E0B")
    C_GREEN    = _c.HexColor("#10B981")
    _AI_COL_W   = 72 * mm
    _BODY_W     = 183 * mm
    _CHART_COL_W = _BODY_W - _AI_COL_W - 5 * mm


# ─────────────────────────────────────────────────────────────────────────────
# Prompt / system context
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_CONTEXT = (
    "You are the Senior Quality Director at AVOCarbon Group.\n"
    "You think and communicate like an executive responsible for customer trust, "
    "operational performance, and business risk.\n\n"
    "ROLE EXPECTATIONS:\n"
    "- You do NOT describe data — you interpret it.\n"
    "- You prioritize risks and decide what matters.\n"
    "- You focus on customer impact, recurrence risk, and operational consequences.\n"
    "- You challenge abnormal situations and do not normalize poor performance.\n\n"
    "COMPANY:\n"
    "AVOCarbon is a global manufacturer of high-performance carbon/graphite sealing and "
    "friction materials (mechanical seals, carbon rings, bushings, vanes, rotary unions, "
    "brake pads, clutch facings). Plants are located in Germany (Frankfurt), "
    "Mexico (Monterrey), France (Poitiers, Amiens), China (Anhui, Tianjin, Kunshan), "
    "Tunisia (Same, Nadhour, SCEET) and India (Chennai).\n\n"
    "KPI DEFINITIONS:\n"
    "- Monthly Complaints: count of new complaints opened in the reporting month.\n"
    "- YTD: cumulative complaints from January 1 to end of reporting month.\n"
    "- Monthly Target: previous-year monthly average × 0.70 (−30% improvement goal).\n"
    "- Open Complaints: complaints in status Open, In Progress, or Under Review.\n"
    "- Avg Days to Close: mean calendar days from opening to closed_at.\n"
    "- Recurrence Rate: % of repetitive complaints.\n"
    "- CS1: quality issue complaint. CS2: warranty claim — higher financial risk.\n\n"
    "OUTPUT FORMAT — STRICT JSON, NO MARKDOWN, VERY CONCISE:\n"
    "{\n"
    '  "headline": "<=12 words, sharp insight",\n'
    '  "summary": "<=80 words — executive synthesis, no filler",\n'
    '  "severity": "<minor|moderate|critical>",\n'
    '  "priority_focus": ["<=8 words each, max 2 items"],\n'
    '  "management_actions": ["WHO + WHAT + WHEN, <=12 words each, max 3 items"]\n'
    "}\n"
)


def _build_prompt(title: str, chart_context: Any, context_note: str,
                  month: int, year: int, scope: str) -> str:
    today   = date.today()
    quarter = (month - 1) // 3 + 1
    m_long  = MONTH_LONG[month - 1]
    active_q = (f"Q{quarter} {year} (months "
                f"{MONTH_SHORT[(quarter-1)*3]}–{['Mar','Jun','Sep','Dec'][quarter-1]})")
    return (
        f"TODAY: {today.strftime('%d %B %Y')}\n"
        f"REPORT MONTH: {m_long} {year}\n"
        f"ACTIVE QUARTER: {active_q}\n"
        f"REPORT SCOPE: {scope}\n"
        f"CHART: {title}\n"
        f"GUIDANCE: {context_note or 'Analyse the chart data and provide management insight.'}\n"
        f"DATA: {json.dumps(chart_context, ensure_ascii=False)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI call
# ─────────────────────────────────────────────────────────────────────────────

def call_openai_chart_summary(
    title: str,
    chart_context: Any,
    context_note: str = "",
    month: int = 0,
    year: int = 0,
    scope: str = "consolidated: all plants",
) -> Optional[Dict[str, Any]]:
    """
    Call the OpenAI API and return a parsed summary dict, or None on failure.
    Returns:
        {headline, summary, severity, priority_focus, management_actions}
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None
    if not os.getenv("OPENAI_API_KEY"):
        return None
    if not month or not year:
        today = date.today()
        month = month or today.month
        year  = year  or today.year
    try:
        client = OpenAI()
        prompt = _build_prompt(title, chart_context, context_note, month, year, scope)
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=f"{_SYSTEM_CONTEXT}\n\n{prompt}",
        )
        text = (getattr(response, "output_text", "") or "").strip()
        if not text:
            return None
        text   = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return None
        return {
            "headline":           str(parsed.get("headline", "")).strip(),
            "summary":            str(parsed.get("summary",  "")).strip(),
            "severity":           str(parsed.get("severity", "")).strip(),
            "priority_focus":     parsed.get("priority_focus", []),
            "management_actions": parsed.get("management_actions", []),
        }
    except Exception as exc:
        logger.debug("AI summary failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ReportLab panel assembly
# ─────────────────────────────────────────────────────────────────────────────

def _severity_colors(severity: str):
    return {
        "critical": (colors.HexColor("#FEE2E2"), C_RED),
        "moderate": (colors.HexColor("#FEF3C7"), C_AMBER),
        "minor":    (colors.HexColor("#D1FAE5"), C_GREEN),
    }.get((severity or "").lower(), (C_FROST, C_ELECTRIC))


def _fit_image(img: Image, max_width: float, max_height: float = None) -> Image:
    """Scale image to fit within max_width (and optionally max_height)."""
    if not img or not isinstance(img, Image):
        return img
    draw_w = getattr(img, "drawWidth",  None) or getattr(img, "imageWidth",  None)
    draw_h = getattr(img, "drawHeight", None) or getattr(img, "imageHeight", None)
    if not draw_w or not draw_h:
        return img
    scale = min(1.0, max_width / float(draw_w))
    if max_height:
        scale = min(scale, max_height / float(draw_h))
    img.drawWidth  = float(draw_w) * scale
    img.drawHeight = float(draw_h) * scale
    return img


def build_ai_panel(summary: Dict, styles: Dict) -> Table:
    """
    Render the AI insight panel as a ReportLab Table.
    `styles` is the dict returned by report_design._S().
    """
    S        = styles
    severity = summary.get("severity", "")
    sev_bg, sev_fg = _severity_colors(severity)

    rows_inner = []

    hdr_style = ParagraphStyle("AIHDR2", fontName="Helvetica-Bold", fontSize=7,
                                textColor=C_WHITE, leading=9.5, spaceAfter=0)
    rows_inner.append([Paragraph("⚡ AI Insight", hdr_style)])

    if severity:
        sev_style = ParagraphStyle("SV2", fontName="Helvetica-Bold", fontSize=6.2,
                                    textColor=sev_fg, leading=8.5, spaceAfter=0)
        rows_inner.append([Paragraph(f"▲ {severity.upper()}", sev_style)])

    rows_inner.append([Paragraph(summary.get("headline", ""), S["ai_head"])])
    rows_inner.append([Paragraph(summary.get("summary",  ""), S["ai_body"])])

    pf = summary.get("priority_focus") or []
    if pf:
        rows_inner.append([Paragraph("Focus", S["ai_label"])])
        for item in pf[:2]:
            short = textwrap.shorten(str(item), width=90, placeholder="…")
            rows_inner.append([Paragraph(f"▸ {short}", S["ai_body"])])

    ma = summary.get("management_actions") or []
    if ma:
        rows_inner.append([Paragraph("Actions", S["ai_label"])])
        for i, item in enumerate(ma[:3], 1):
            short = textwrap.shorten(str(item), width=110, placeholder="…")
            rows_inner.append([Paragraph(f"{i}. {short}", S["ai_action"])])

    panel_w = _AI_COL_W - 5 * mm

    inner = Table([[r[0]] for r in rows_inner], colWidths=[panel_w - 10])
    n = len(rows_inner)
    cmd = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_NAVY),
        ("BACKGROUND",    (0, 1), (-1, -1), C_PAPER),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]
    if severity and n > 1:
        cmd.append(("BACKGROUND", (0, 1), (-1, 1), sev_bg))
    inner.setStyle(TableStyle(cmd))

    stripe_cell = Spacer(4 * mm, 1)
    content_row = Table(
        [[stripe_cell, inner]],
        colWidths=[4 * mm, panel_w],
        style=TableStyle([
            ("BACKGROUND",    (0, 0), (0, -1), sev_fg),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ]),
    )

    outer = Table([[content_row]], colWidths=[_AI_COL_W])
    outer.setStyle(TableStyle([
        ("BOX",           (0, 0), (-1, -1), 0.6, C_MIST),
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    return outer


# ─────────────────────────────────────────────────────────────────────────────
# High-level story helpers  (called from report_builder)
# ─────────────────────────────────────────────────────────────────────────────

def chart_with_ai_right(chart_img: Image, summary: Dict, styles: Dict) -> Optional[Table]:
    """Chart left + AI panel right, side by side."""
    if not chart_img and not summary:
        return None
    MAX_H = 150 * mm
    left  = _fit_image(chart_img, _CHART_COL_W, MAX_H) if chart_img else Spacer(1, 1)
    right = build_ai_panel(summary, styles) if summary else Spacer(1, 1)
    tbl   = Table(
        [[left, right]],
        colWidths=[_CHART_COL_W + 2, _AI_COL_W + 2],
        splitByRow=1,
    )
    tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 2),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 2),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tbl


def standalone_ai_box(summary: Dict, styles: Dict) -> Optional[KeepTogether]:
    """Full-width AI panel with no chart."""
    if not summary:
        return None
    panel = build_ai_panel(summary, styles)
    full  = Table([[panel]], colWidths=[_BODY_W])
    full.setStyle(TableStyle([
        ("TOPPADDING",    (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
    ]))
    return KeepTogether([full, Spacer(1, 2 * mm)])


def append_ai_summary(
    story: list,
    styles: Dict,
    title: str,
    chart_context: Any,
    context_note: str = "",
    month: int = 0,
    year: int = 0,
    scope: str = "consolidated: all plants",
    chart_img: Optional[Image] = None,
) -> None:
    """
    Fetch AI summary then append the correct combination to story:
      - chart + AI panel  (side by side)
      - chart alone       (full width, no AI available)
      - AI panel alone    (no chart)
      - nothing           (both absent)

    IMPORTANT: chart_img must NOT have been added to story yet.
    This is the sole place it enters the story.
    """
    summary = call_openai_chart_summary(
        title, chart_context,
        context_note=context_note,
        month=month, year=year, scope=scope,
    )

    if chart_img and summary:
        block = chart_with_ai_right(chart_img, summary, styles)
        if block:
            story.append(block)
    elif chart_img:
        story.append(_fit_image(chart_img, _BODY_W))
    elif summary:
        box = standalone_ai_box(summary, styles)
        if box:
            story.append(box)
    # both None → append nothing