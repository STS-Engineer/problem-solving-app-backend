"""
app/services/kpi_report_pdf.py  ── v3
──────────────────────────────────────
Monthly KPI PDF in the AVOCarbon brand palette (deep navy-green + gold).

Two variants:
  • per_plant_report(data, plant, month, year) -> bytes
  • consolidated_report(data, month, year)     -> bytes

`data` is the dict returned by DashboardService.get_dashboard_stats().

New in v2:
  ✦ Full dark navy-green (#1A3A2A) brand palette – matches avocarbon.com
  ✦ Logo fetched from CDN (with fallback SVG text-mark)
  ✦ Per-plant: + defect types, + valeo trend, + cost by D-step,
                 + status donut, + quarterly bar
  ✦ Consolidated: + all-plant heatmap, + product-line stacked bar,
                    + defect types group bar, + cost by step table
  ✦ Cover page for consolidated report
  ✦ Section divider banners
  ✦ Two-column layout for small charts
"""

from __future__ import annotations

import io
import logging
import os
import textwrap
import urllib.request
import matplotlib.pyplot as plt
import numpy as np
from datetime import date
from typing import Any, Dict, List, Optional
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
import matplotlib

matplotlib.use("Agg")

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Brand Palette  –  AVOCarbon deep navy-green + gold
# ─────────────────────────────────────────────────────────────────────────────
AVO_NAVY = colors.HexColor("#1A3A2A")  # deep navy-green  (header / primary)
AVO_GREEN = colors.HexColor("#2E6B47")  # mid forest green
AVO_GREEN_MID = colors.HexColor("#4A7C59")  # body accent
AVO_GREEN_LIGHT = colors.HexColor("#A8C5A0")  # subtle green tint
AVO_GOLD = colors.HexColor("#D4A843")  # accent gold
AVO_GOLD_LIGHT = colors.HexColor("#F0D080")  # light gold
AVO_CHARCOAL = colors.HexColor("#1E1E1E")  # almost black text
AVO_GREY = colors.HexColor("#5A6472")  # muted label
AVO_LIGHT_GREY = colors.HexColor("#F0F2F0")  # row zebra
AVO_WHITE = colors.white
AVO_RED = colors.HexColor("#B03030")  # alert
AVO_RED_LIGHT = colors.HexColor("#F5DADA")

# Matplotlib equivalents
_N = "#1A3A2A"  # navy
_G = "#2E6B47"  # green
_GL = "#A8C5A0"  # light green
_AU = "#D4A843"  # gold
_CH = "#1E1E1E"  # charcoal
_GR = "#5A6472"  # grey
_LG = "#F0F2F0"  # light grey
_RE = "#B03030"  # red
_BL = "#2C5F8A"  # steel blue (secondary accent)
_OR = "#C06020"  # orange (tertiary)

PALETTE = [_N, _AU, _G, _RE, _BL, _OR, _GL, "#8E5FA0", "#607080", "#A06040", "#40A0A0"]
MONTH_SHORT = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic target  =  (last-year total / 12) × 0.70   (-30 % vs N-1 avg)
# Falls back to static MONTHLY_TARGETS_2026 when no last-year data exists.
# ─────────────────────────────────────────────────────────────────────────────
_STATIC_TARGETS: Dict[str, int] = {
    "FRANKFURT": 4,
    "SCEET": 2,
    "ASSYMEX": 2,
    "CHENNAI": 1,
    "TIANJIN": 1,
    "DAEGU": 1,
    "ANHUI": 1,
    "Kunshan": 1,
    "SAME": 0,
    "POITIERS": 0,
    "CYCLAM": 0,
}


def _compute_target(data: Dict[str, Any], plant: str) -> float:
    """
    Returns the monthly target for `plant`:
      • If data contains "last_year_by_plant" with a count > 0 for this plant:
          target = (last_year_count / 12) * 0.70   (−30 % of last-year monthly avg)
      • Otherwise falls back to the static _STATIC_TARGETS table.

    dashboard_service must add:
        "last_year_by_plant": DashboardService._get_total_by_plant(
            db, DashboardService._build_filter(year - 1))
    to the returned dict.
    """
    last_year = data.get("last_year_by_plant", [])
    row = next((r for r in last_year if r.get("plant") == plant), None)
    if row and row.get("count", 0) > 0:
        return round((row["count"] / 12) * 0.70, 2)
    return float(_STATIC_TARGETS.get(plant, 0))


MONTH_LONG = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
PAGE_W, PAGE_H = A4

# ─────────────────────────────────────────────────────────────────────────────
# Logo loader  (fetched from CDN; cached in module scope)
# ─────────────────────────────────────────────────────────────────────────────
_LOGO_URL = "https://avocarbon-customer-complaint.azurewebsites.net/assets/logo-avocarbon-BPLJ2lDY.png"
_LOGO_PATH = "/tmp/_avo_logo_cached.png"
_logo_bytes: Optional[bytes] = None


def _get_logo() -> Optional[bytes]:
    global _logo_bytes
    if _logo_bytes is not None:
        return _logo_bytes
    # 1. Try disk cache
    if os.path.exists(_LOGO_PATH):
        with open(_LOGO_PATH, "rb") as f:
            _logo_bytes = f.read()
        return _logo_bytes
    # 2. Try network
    try:
        req = urllib.request.Request(_LOGO_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            _logo_bytes = resp.read()
        with open(_LOGO_PATH, "wb") as f:
            f.write(_logo_bytes)
        logger.info("AVOCarbon logo downloaded OK (%d bytes)", len(_logo_bytes))
        return _logo_bytes
    except Exception as exc:
        logger.warning("Logo fetch failed (%s) — using text fallback", exc)
        return None


def _logo_image(w_cm: float = 4.0, h_cm: float = 1.3) -> Optional[Image]:
    data = _get_logo()
    if data:
        buf = io.BytesIO(data)
        return Image(buf, width=w_cm * cm, height=h_cm * cm)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ReportLab styles
# ─────────────────────────────────────────────────────────────────────────────


def _S() -> Dict[str, ParagraphStyle]:
    return {
        "section": ParagraphStyle(
            "Sec",
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=AVO_NAVY,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "section_sm": ParagraphStyle(
            "SecS",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=AVO_GREEN,
            spaceBefore=8,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=8, textColor=AVO_CHARCOAL, leading=12
        ),
        "kpi_val": ParagraphStyle(
            "KV",
            fontName="Helvetica-Bold",
            fontSize=24,
            textColor=AVO_NAVY,
            alignment=TA_CENTER,
        ),
        "kpi_lbl": ParagraphStyle(
            "KL",
            fontName="Helvetica",
            fontSize=7,
            textColor=AVO_GREY,
            alignment=TA_CENTER,
        ),
        "kpi_delta": ParagraphStyle(
            "KD", fontName="Helvetica-Bold", fontSize=7, alignment=TA_CENTER
        ),
        "th": ParagraphStyle(
            "TH",
            fontName="Helvetica-Bold",
            fontSize=7,
            textColor=AVO_WHITE,
            alignment=TA_CENTER,
        ),
        "td": ParagraphStyle(
            "TD",
            fontName="Helvetica",
            fontSize=7,
            textColor=AVO_CHARCOAL,
            alignment=TA_CENTER,
        ),
        "td_l": ParagraphStyle(
            "TDL",
            fontName="Helvetica",
            fontSize=7,
            textColor=AVO_CHARCOAL,
            alignment=TA_LEFT,
        ),
        "cover_title": ParagraphStyle(
            "CT",
            fontName="Helvetica-Bold",
            fontSize=28,
            textColor=AVO_WHITE,
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "cover_sub": ParagraphStyle(
            "CS",
            fontName="Helvetica",
            fontSize=13,
            textColor=AVO_GOLD,
            alignment=TA_CENTER,
        ),
        "cover_plant": ParagraphStyle(
            "CP",
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=AVO_GOLD,
            alignment=TA_CENTER,
            spaceAfter=4,
        ),
        "footer": ParagraphStyle(
            "Ft",
            fontName="Helvetica",
            fontSize=6,
            textColor=AVO_GREY,
            alignment=TA_CENTER,
        ),
    }


def _tbl_style(has_total: bool = False, zebra: bool = True) -> TableStyle:
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), AVO_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), AVO_WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("GRID", (0, 0), (-1, -1), 0.25, AVO_GREEN_LIGHT),
    ]
    if zebra:
        cmds.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [AVO_WHITE, AVO_LIGHT_GREY]))
    if has_total:
        cmds += [
            ("BACKGROUND", (0, -1), (-1, -1), AVO_GREEN_LIGHT),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, -1), (-1, -1), AVO_NAVY),
        ]
    return TableStyle(cmds)


# ─────────────────────────────────────────────────────────────────────────────
# Chart helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fig_to_img(fig, w_cm: float = 16, h_cm: float = 6) -> Image:
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=140,
        bbox_inches="tight",
        facecolor="none",
        transparent=True,
    )
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=w_cm * cm, height=h_cm * cm)


def _apply_avo_style(ax):
    ax.set_facecolor("none")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(axis="both", labelsize=7, colors=_CH)
    ax.yaxis.grid(True, linestyle="--", alpha=0.35, color=_GL, zorder=0)
    ax.set_axisbelow(True)


def _bar_monthly(
    monthly_data: List[Dict],
    plant: Optional[str] = None,
    title: str = "",
    target: Optional[float] = None,
) -> Image:
    """Stacked (all-plants) or single-plant monthly bar.
    When `plant` + `target` are given a dashed target line is drawn.
    """
    months = [d["month"] for d in monthly_data]
    plant_keys = [
        k
        for k in (monthly_data[0] if monthly_data else {})
        if k not in ("month", "total")
    ]
    fig, ax = plt.subplots(figsize=(11, 3.8))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)

    if plant:
        vals = [d.get(plant, 0) for d in monthly_data]
        bars = ax.bar(
            months,
            vals,
            color=_G,
            alpha=0.88,
            width=0.6,
            zorder=3,
            edgecolor=_N,
            linewidth=0.4,
        )
        for b, v in zip(bars, vals):
            if v:
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    b.get_height() + 0.08,
                    str(v),
                    ha="center",
                    va="bottom",
                    fontsize=6.5,
                    color=_N,
                    fontweight="bold",
                )
        # ── Dynamic target line ──
        if target is not None and target > 0:
            lbl = f"Target  {target:.1f}  (−30 % last-yr avg)"
            ax.axhline(
                target, color=_AU, linestyle="--", linewidth=1.8, zorder=4, label=lbl
            )
            ax.legend(fontsize=6.5, framealpha=0.92, edgecolor=_GL, loc="upper right")
    else:
        bottoms = np.zeros(len(months))
        for i, p in enumerate(plant_keys):
            vals = np.array([d.get(p, 0) for d in monthly_data])
            ax.bar(
                months,
                vals,
                bottom=bottoms,
                color=PALETTE[i % len(PALETTE)],
                label=p,
                alpha=0.85,
                width=0.7,
                zorder=3,
                edgecolor="white",
                linewidth=0.3,
            )
            bottoms += vals
        ax.legend(
            fontsize=5.5,
            loc="upper right",
            framealpha=0.9,
            ncol=4,
            edgecolor=_GL,
            handlelength=1.2,
        )

    ax.set_title(title, fontsize=9, fontweight="bold", color=_N, pad=5)
    ax.set_ylabel("Complaints", fontsize=7.5, color=_GR)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 16, 5)


def _pie(labels, values, title, center_text: str = "") -> Image:
    fig, ax = plt.subplots(figsize=(5.5, 4.5))
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    non_z = [(l, v) for l, v in zip(labels, values) if v > 0]
    if not non_z:
        ax.text(
            0.5,
            0.5,
            "No data",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=9,
            color=_GR,
        )
        ax.axis("off")
    else:
        ls, vs = zip(*non_z)
        wedges, _, auts = ax.pie(
            vs,
            colors=PALETTE[: len(ls)],
            autopct="%1.0f%%",
            startangle=90,
            pctdistance=0.78,
            wedgeprops=dict(linewidth=0.7, edgecolor="white"),
            textprops=dict(fontsize=6.5),
        )
        for at in auts:
            at.set_fontweight("bold")
        if center_text:
            ax.text(
                0,
                0,
                center_text,
                ha="center",
                va="center",
                fontsize=9,
                fontweight="bold",
                color=_N,
            )
        ax.legend(
            wedges,
            [f"{l} ({v})" for l, v in zip(ls, vs)],
            fontsize=5.5,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.18),
            ncol=2,
            framealpha=0.9,
            edgecolor=_GL,
        )
    ax.set_title(title, fontsize=8.5, fontweight="bold", color=_N, pad=4)
    fig.tight_layout(pad=0.3)
    return _fig_to_img(fig, 8.5, 6.5)


def _donut(labels, values, title, center_text="") -> Image:
    fig, ax = plt.subplots(figsize=(4.5, 4))
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    non_z = [(l, v) for l, v in zip(labels, values) if v > 0]
    if not non_z:
        ax.text(
            0.5,
            0.5,
            "No data",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=9,
            color=_GR,
        )
        ax.axis("off")
    else:
        ls, vs = zip(*non_z)
        status_colors = {
            "open": _RE,
            "in_progress": _AU,
            "under_review": _BL,
            "resolved": _G,
            "closed": _N,
            "rejected": _GR,
        }
        clrs = [
            status_colors.get(l.lower().replace(" ", "_"), PALETTE[i % len(PALETTE)])
            for i, l in enumerate(ls)
        ]
        wedges, _ = ax.pie(
            vs,
            colors=clrs,
            startangle=90,
            wedgeprops=dict(width=0.52, linewidth=0.7, edgecolor="white"),
        )
        total = sum(vs)
        ax.text(
            0,
            0,
            f"{total}\nTotal",
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            color=_N,
            linespacing=1.3,
        )
        ax.legend(
            wedges,
            [f"{l} ({v})" for l, v in zip(ls, vs)],
            fontsize=5.5,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.2),
            ncol=2,
            framealpha=0.9,
            edgecolor=_GL,
        )
    ax.set_title(title, fontsize=8.5, fontweight="bold", color=_N, pad=4)
    fig.tight_layout(pad=0.3)
    return _fig_to_img(fig, 7.5, 5.5)


def _hbar(labels, values, title, color=_G, value_prefix="") -> Image:
    n = max(len(labels), 1)
    fig, ax = plt.subplots(figsize=(8, max(3, n * 0.42 + 0.8)))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)
    y = range(n)
    bars = ax.barh(
        list(y),
        values,
        color=color,
        alpha=0.87,
        height=0.55,
        edgecolor=_N,
        linewidth=0.3,
    )
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_title(title, fontsize=8.5, fontweight="bold", color=_N, pad=4)
    ax.xaxis.grid(True, linestyle="--", alpha=0.35, color=_GL)
    for b, v in zip(bars, values):
        ax.text(
            b.get_width() + max(values) * 0.01 if max(values) else 0.05,
            b.get_y() + b.get_height() / 2,
            f"{value_prefix}{v}",
            va="center",
            fontsize=6.5,
            color=_N,
        )
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 10, max(3.8, n * 0.5 + 1))


def _line_vs_target(
    rows: List[Dict], plant: str, dyn_target: Optional[float] = None
) -> Optional[Image]:
    """Actual vs target line chart.
    `dyn_target` (−30 % of last-yr avg) overrides the static value in rows.
    """
    pr = [r for r in rows if r.get("plant") == plant]
    if not pr:
        return None
    months = [r["month"] for r in pr]
    actual = [r["actual"] for r in pr]
    target_val = (
        dyn_target if (dyn_target is not None and dyn_target > 0) else pr[0]["target"]
    )
    target = [target_val] * len(months)
    lbl_target = (
        f"Target  {target_val:.1f}  (−30 % last-yr avg)"
        if (dyn_target is not None and dyn_target > 0)
        else f"Target ({target_val})"
    )
    fig, ax = plt.subplots(figsize=(10, 3.5))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)
    ax.plot(months, actual, color=_G, marker="o", lw=2, ms=5, label="Actual", zorder=3)
    ax.plot(months, target, color=_AU, ls="--", lw=1.8, label=lbl_target, zorder=2)
    ax.fill_between(
        months,
        actual,
        target,
        where=[a > t for a, t in zip(actual, target)],
        alpha=0.18,
        color=_RE,
        label="Above target",
    )
    ax.fill_between(
        months,
        actual,
        target,
        where=[a <= t for a, t in zip(actual, target)],
        alpha=0.12,
        color=_G,
        label="On target",
    )
    ax.legend(fontsize=6.5, framealpha=0.9, edgecolor=_GL)
    ax.set_title(
        f"{plant} — Actual vs Monthly Target", fontsize=9, fontweight="bold", color=_N
    )
    ax.tick_params(labelsize=7)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 16, 4.5)


def _cs_grouped_bar(cs_rows: List[Dict], plant: str, title: str) -> Image:
    months = [r["month"] for r in cs_rows]
    cs1 = [r["CS1"] for r in cs_rows]
    cs2 = [r["CS2"] for r in cs_rows]
    fig, ax = plt.subplots(figsize=(11, 3.5))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)
    x = np.arange(len(months))
    w = 0.38
    ax.bar(x - w / 2, cs1, w, label="CS1", color=_G, alpha=0.87, edgecolor=_N, lw=0.3)
    ax.bar(x + w / 2, cs2, w, label="CS2", color=_AU, alpha=0.87, edgecolor=_N, lw=0.3)
    ax.set_xticks(x)
    ax.set_xticklabels(months, fontsize=6.5)
    ax.legend(fontsize=7, framealpha=0.9, edgecolor=_GL)
    ax.set_title(title, fontsize=9, fontweight="bold", color=_N)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 16, 4.5)


def _open_closed_area(oc_rows: List[Dict], title: str) -> Image:
    mo = [r["month"] for r in oc_rows]
    opens = [r["open"] for r in oc_rows]
    closed = [r["closed"] for r in oc_rows]
    fig, ax = plt.subplots(figsize=(11, 3.5))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)
    ax.stackplot(
        mo, opens, closed, labels=["Open", "Closed"], colors=[_RE, _G], alpha=0.72
    )
    ax.legend(fontsize=7, loc="upper right", framealpha=0.9, edgecolor=_GL)
    ax.set_title(title, fontsize=9, fontweight="bold", color=_N)
    ax.tick_params(labelsize=6.5)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 16, 4.5)


def _quarterly_grouped(
    quarterly: List[Dict], plant: Optional[str] = None, title: str = "Quarterly"
) -> Image:
    plant_keys = [
        k for k in (quarterly[0] if quarterly else {}) if k not in ("quarter", "total")
    ]
    quarters = [q["quarter"] for q in quarterly]
    fig, ax = plt.subplots(figsize=(8, 3.5))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)
    if plant:
        vals = [q.get(plant, 0) for q in quarterly]
        bars = ax.bar(quarters, vals, color=PALETTE, alpha=0.87, edgecolor=_N, lw=0.3)
        for b, v in zip(bars, vals):
            if v:
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    b.get_height() + 0.06,
                    str(v),
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    color=_N,
                    fontweight="bold",
                )
    else:
        x = np.arange(len(quarters))
        w = 0.7 / max(len(plant_keys), 1)
        for i, p in enumerate(plant_keys):
            vals = [q.get(p, 0) for q in quarterly]
            ax.bar(
                x + i * w - len(plant_keys) * w / 2,
                vals,
                w,
                color=PALETTE[i % len(PALETTE)],
                label=p,
                alpha=0.87,
                edgecolor="white",
                lw=0.2,
            )
        ax.set_xticks(x)
        ax.set_xticklabels(quarters, fontsize=7)
        ax.legend(fontsize=5.5, ncol=4, framealpha=0.9, edgecolor=_GL)
    ax.set_title(title, fontsize=9, fontweight="bold", color=_N)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 10, 4.5)


def _defect_bar(defect_types: List[Dict], title: str, top_n: int = 12) -> Image:
    top = sorted(defect_types, key=lambda x: x["count"], reverse=True)[:top_n]
    if not top:
        return None
    labels = [textwrap.shorten(r["type"], 28) for r in top]
    vals = [r["count"] for r in top]
    return _hbar(labels[::-1], vals[::-1], title, color=_BL)


def _valeo_line(valeo_monthly: List[Dict], title: str) -> Image:
    months = [r["month"] for r in valeo_monthly]
    vals = [r["count"] for r in valeo_monthly]
    fig, ax = plt.subplots(figsize=(10, 3))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)
    ax.plot(months, vals, color=_AU, marker="D", lw=2, ms=5, zorder=3)
    ax.fill_between(months, vals, alpha=0.15, color=_AU)
    for m, v in zip(months, vals):
        if v:
            ax.text(
                m,
                v + 0.1,
                str(v),
                ha="center",
                va="bottom",
                fontsize=6.5,
                color=_AU,
                fontweight="bold",
            )
    ax.set_title(title, fontsize=9, fontweight="bold", color=_N)
    ax.tick_params(labelsize=6.5)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 16, 4)


def _cost_bar(
    cost_rows: List[Dict], plant: Optional[str] = None, title: str = "D-Step Costs"
) -> Optional[Image]:
    steps = [f"D{i}" for i in range(1, 9)]
    if plant:
        row = next((r for r in cost_rows if r.get("plant") == plant), None)
        if not row or row.get("total", 0) == 0:
            return None
        vals = [row.get(s, 0) for s in steps]
        fig, ax = plt.subplots(figsize=(9, 3.2))
        fig.patch.set_alpha(0)
        _apply_avo_style(ax)
        bars = ax.bar(steps, vals, color=PALETTE[:8], alpha=0.87, edgecolor=_N, lw=0.3)
        for b, v in zip(bars, vals):
            if v:
                ax.text(
                    b.get_x() + b.get_width() / 2,
                    b.get_height() + max(vals) * 0.01,
                    f"€{v:,}",
                    ha="center",
                    va="bottom",
                    fontsize=6,
                    color=_N,
                )
        ax.set_title(title, fontsize=9, fontweight="bold", color=_N)
        ax.set_ylabel("€", fontsize=7, color=_GR)
        fig.tight_layout(pad=0.5)
        return _fig_to_img(fig, 12, 4)
    else:
        # stacked bar per plant
        if not cost_rows:
            return None
        plants = [r["plant"] for r in cost_rows]
        fig, ax = plt.subplots(figsize=(11, 4))
        fig.patch.set_alpha(0)
        _apply_avo_style(ax)
        bottoms = np.zeros(len(plants))
        for i, s in enumerate(steps):
            vals = np.array([r.get(s, 0) for r in cost_rows])
            ax.bar(
                plants,
                vals,
                bottom=bottoms,
                color=PALETTE[i % len(PALETTE)],
                label=s,
                alpha=0.87,
                edgecolor="white",
                lw=0.2,
            )
            bottoms += vals
        ax.legend(fontsize=6, ncol=8, framealpha=0.9, edgecolor=_GL)
        ax.set_title(title, fontsize=9, fontweight="bold", color=_N)
        ax.set_ylabel("€", fontsize=7, color=_GR)
        ax.tick_params(axis="x", rotation=20, labelsize=7)
        fig.tight_layout(pad=0.5)
        return _fig_to_img(fig, 16, 5)


def _heatmap_customer_plant(
    cust_plant_data: List[Dict], plants: List[str]
) -> Optional[Image]:
    if not cust_plant_data:
        return None
    top = cust_plant_data[:15]
    customers = [r["customer"] for r in top]
    matrix = np.array([[r.get(p, 0) for p in plants] for r in top], dtype=float)
    fig, ax = plt.subplots(figsize=(12, max(4, len(customers) * 0.45 + 1.5)))
    fig.patch.set_alpha(0)
    import matplotlib.colors as mcolors

    cmap = mcolors.LinearSegmentedColormap.from_list("avo", [_LG, _GL, _G, _N])
    im = ax.imshow(matrix, aspect="auto", cmap=cmap)
    ax.set_xticks(range(len(plants)))
    ax.set_xticklabels(plants, fontsize=6.5, rotation=30, ha="right")
    ax.set_yticks(range(len(customers)))
    ax.set_yticklabels(customers, fontsize=6.5)
    for i in range(len(customers)):
        for j in range(len(plants)):
            v = int(matrix[i, j])
            if v:
                ax.text(
                    j,
                    i,
                    str(v),
                    ha="center",
                    va="center",
                    fontsize=6,
                    color="white" if matrix[i, j] > matrix.max() * 0.5 else _N,
                    fontweight="bold",
                )
    plt.colorbar(im, ax=ax, fraction=0.02, pad=0.02, label="Complaints")
    ax.set_title("Customer × Plant Heatmap", fontsize=9, fontweight="bold", color=_N)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 16, max(5, len(customers) * 0.55 + 2))


def _product_line_stacked(pl_plant: List[Dict], plants: List[str]) -> Optional[Image]:
    if not pl_plant:
        return None
    pl_labels = [r["product_line"] for r in pl_plant]
    fig, ax = plt.subplots(figsize=(10, max(3.5, len(pl_labels) * 0.55 + 1.5)))
    fig.patch.set_alpha(0)
    _apply_avo_style(ax)
    bottoms = np.zeros(len(pl_labels))
    for i, p in enumerate(plants):
        vals = np.array([r.get(p, 0) for r in pl_plant])
        ax.barh(
            pl_labels,
            vals,
            left=bottoms,
            color=PALETTE[i % len(PALETTE)],
            label=p,
            alpha=0.87,
            height=0.6,
            edgecolor="white",
            linewidth=0.2,
        )
        bottoms += vals
    ax.legend(fontsize=5.5, loc="lower right", framealpha=0.9, edgecolor=_GL, ncol=3)
    ax.set_title(
        "Complaints by Product Line × Plant", fontsize=9, fontweight="bold", color=_N
    )
    ax.tick_params(labelsize=7)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 14, max(4, len(pl_labels) * 0.65 + 1.5))


# ─────────────────────────────────────────────────────────────────────────────
# Page layout  –  header / footer via onPage callback
# ─────────────────────────────────────────────────────────────────────────────


def _build_doc(buf: io.BytesIO, title: str, month: int, year: int) -> BaseDocTemplate:
    m_short = MONTH_SHORT[month - 1]
    logo_data = _get_logo()

    def _hf(canvas, doc):
        canvas.saveState()
        # ── Header ──
        canvas.setFillColor(AVO_NAVY)
        canvas.rect(0, PAGE_H - 26 * mm, PAGE_W, 26 * mm, fill=1, stroke=0)
        # gold accent stripe
        canvas.setFillColor(AVO_GOLD)
        canvas.rect(0, PAGE_H - 27.5 * mm, PAGE_W, 1.5 * mm, fill=1, stroke=0)

        # Logo (left side of header)
        if logo_data:
            try:
                buf_logo = io.BytesIO(logo_data)
                canvas.drawImage(
                    buf_logo,
                    12 * mm,
                    PAGE_H - 22 * mm,
                    width=3.5 * cm,
                    height=1.1 * cm,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            except Exception:
                _draw_text_logo(canvas, 12 * mm, PAGE_H - 18 * mm)
        else:
            _draw_text_logo(canvas, 12 * mm, PAGE_H - 18 * mm)

        # Title
        canvas.setFillColor(AVO_WHITE)
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawCentredString(PAGE_W / 2, PAGE_H - 14 * mm, title)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(AVO_GOLD)
        canvas.drawCentredString(
            PAGE_W / 2,
            PAGE_H - 20 * mm,
            f"{m_short} {year}  ·  Monthly Quality KPI Report",
        )

        # Page number (right of header)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(AVO_GOLD_LIGHT)
        canvas.drawRightString(PAGE_W - 12 * mm, PAGE_H - 16 * mm, f"p. {doc.page}")

        # ── Footer ──
        canvas.setStrokeColor(AVO_GREEN_LIGHT)
        canvas.setLineWidth(0.4)
        canvas.line(12 * mm, 12 * mm, PAGE_W - 12 * mm, 12 * mm)
        canvas.setFillColor(AVO_GREY)
        canvas.setFont("Helvetica", 6)
        canvas.drawString(
            12 * mm, 7 * mm, "AVOCarbon Quality Management System — Confidential"
        )
        canvas.drawCentredString(
            PAGE_W / 2, 7 * mm, f"Generated {date.today().strftime('%d %B %Y')}"
        )
        canvas.drawRightString(PAGE_W - 12 * mm, 7 * mm, "www.avocarbon.com")
        canvas.restoreState()

    frame = Frame(
        12 * mm,
        18 * mm,
        PAGE_W - 24 * mm,
        PAGE_H - 50 * mm,
        id="body",
        leftPadding=0,
        rightPadding=0,
        topPadding=3,
        bottomPadding=3,
    )
    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=33 * mm,
        bottomMargin=20 * mm,
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_hf)])
    return doc


def _draw_text_logo(canvas, x, y):
    """Fallback text logo when PNG not available."""
    canvas.setFillColor(AVO_GOLD)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(x, y, "AVO")
    canvas.setFillColor(AVO_WHITE)
    canvas.setFont("Helvetica-Bold", 13)
    canvas.drawString(x + 27, y, "CARBON")


# ─────────────────────────────────────────────────────────────────────────────
# Section banner
# ─────────────────────────────────────────────────────────────────────────────


def _section_banner(text: str, S: Dict) -> List:
    """Green banner acting as a section header."""
    return [
        Spacer(1, 4 * mm),
        Table(
            [
                [
                    Paragraph(
                        text,
                        ParagraphStyle(
                            "SBan",
                            fontName="Helvetica-Bold",
                            fontSize=10,
                            textColor=AVO_WHITE,
                            alignment=TA_LEFT,
                        ),
                    )
                ]
            ],
            colWidths=[PAGE_W - 24 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), AVO_GREEN),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            ),
        ),
        Spacer(1, 3 * mm),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# KPI card row
# ─────────────────────────────────────────────────────────────────────────────


def _kpi_cards(metrics: List[Dict]) -> Table:
    S = _S()
    col_w = (PAGE_W - 24 * mm) / len(metrics)
    cells = []
    for m in metrics:
        val_str = str(m["value"])
        delta = str(m.get("delta", ""))
        is_bad = delta.startswith("+") and "target" in delta.lower()
        delta_color = (
            f"#{AVO_RED.hexval()[2:]}" if is_bad else f"#{AVO_GREEN.hexval()[2:]}"
        )
        cell = [Paragraph(val_str, S["kpi_val"]), Paragraph(m["label"], S["kpi_lbl"])]
        if delta:
            cell.append(
                Paragraph(
                    f'<font color="{delta_color}"><b>{delta}</b></font>', S["kpi_delta"]
                )
            )
        cells.append(cell)
    tbl = Table([cells], colWidths=[col_w] * len(metrics))
    tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, AVO_GREEN_LIGHT),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, AVO_GREEN_LIGHT),
                ("BACKGROUND", (0, 0), (-1, -1), AVO_LIGHT_GREY),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return tbl


# ─────────────────────────────────────────────────────────────────────────────
# Two-column layout helper
# ─────────────────────────────────────────────────────────────────────────────


def _two_col(left_img: Optional[Image], right_img: Optional[Image]) -> Optional[Table]:
    if not left_img and not right_img:
        return None
    l = left_img if left_img else Spacer(1, 1)
    r = right_img if right_img else Spacer(1, 1)
    half = (PAGE_W - 24 * mm) / 2
    tbl = Table([[l, r]], colWidths=[half, half])
    tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return tbl


# ─────────────────────────────────────────────────────────────────────────────
# PER-PLANT REPORT
# ─────────────────────────────────────────────────────────────────────────────


def per_plant_report(data: Dict[str, Any], plant: str, month: int, year: int) -> bytes:
    S = _S()
    buf = io.BytesIO()
    doc = _build_doc(buf, f"AVOCarbon  ·  {plant}  ·  KPI Report", month, year)
    story: list = []
    m_name = MONTH_SHORT[month - 1]
    m_long = MONTH_LONG[month - 1]

    monthly_data = data.get("monthly_data", [])
    monthly_vs_target = data.get("monthly_vs_target", [])
    overdue_by_plant = data.get("overdue_complaints", {}).get("by_plant", [])
    cqt_by_plant = data.get("cqt_lateness", {}).get("by_plant", [])
    cqt_by_cqt = data.get("cqt_lateness", {}).get("by_cqt", [])
    overdue_steps = data.get("overdue_steps", [])
    status_monthly = data.get("status_monthly", [])
    cs_all = data.get("cs_type_per_plant_monthly", [])
    oc_all = data.get("open_closed_per_plant_monthly", [])
    rep_by_plant = data.get("repetitive_by_plant", [])
    defect_types = data.get("defect_types", [])
    valeo_monthly = data.get("valeo_monthly", [])
    cost_by_step = data.get("cost_by_step_plant", [])
    pl_plant_data = data.get("complaints_by_product_line_plant", [])
    quarterly = data.get("quarterly_by_plant", [])
    cust_avocarbon = data.get("complaints_per_customer_avocarbon", [])

    # ── Compute KPIs ──
    m_count = next((r.get(plant, 0) for r in monthly_data if r["month"] == m_name), 0)
    target = _compute_target(data, plant)
    ytd = sum(r.get(plant, 0) for r in monthly_data)
    overdue = next((r["count"] for r in overdue_by_plant if r["plant"] == plant), 0)
    late_cqt = next(
        (r["late_complaints"] for r in cqt_by_plant if r["plant"] == plant), 0
    )
    target_label = (
        f"{target:.1f}  (−30 % last-yr avg)"
        if data.get("last_year_by_plant")
        else str(int(target))
    )
    delta_str = (
        f"+{m_count-target:.1f} vs target"
        if m_count > target
        else f"-{target-m_count:.1f} vs target"
    )

    # ── KPI row ──
    story.append(Spacer(1, 3 * mm))
    story.append(
        _kpi_cards(
            [
                {"label": f"{m_long} Complaints", "value": m_count, "delta": delta_str},
                {"label": f"YTD {year}", "value": ytd, "delta": ""},
                {"label": "Monthly Target", "value": target_label, "delta": ""},
                {"label": "Overdue Complaints", "value": overdue, "delta": ""},
                {"label": "CQT Late (complaints)", "value": late_cqt, "delta": ""},
            ]
        )
    )
    story.append(Spacer(1, 5 * mm))

    # ────────────────────────────────────────────────────
    # 1. MONTHLY TREND + vs TARGET
    # ────────────────────────────────────────────────────
    story += _section_banner("📈  Monthly Trend", S)
    story.append(
        _bar_monthly(
            monthly_data,
            plant=plant,
            title=f"{plant} — Complaints per Month {year}",
            target=target,
        )
    )
    story.append(Spacer(1, 3 * mm))
    vs_img = _line_vs_target(monthly_vs_target, plant, dyn_target=target)
    if vs_img:
        story.append(vs_img)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 2. QUARTERLY BREAKDOWN
    # ────────────────────────────────────────────────────
    story += _section_banner("📊  Quarterly Breakdown", S)
    q_img = _quarterly_grouped(
        quarterly, plant=plant, title=f"{plant} — Complaints by Quarter {year}"
    )
    if q_img:
        story.append(q_img)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 3. CUSTOMERS + PRODUCT LINES  (two-col)
    # ────────────────────────────────────────────────────
    story += _section_banner("👥  Customers & Product Lines", S)
    cust_rows = sorted(
        [r for r in cust_avocarbon if r.get("avocarbon_plant") == plant],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]
    c_img = None
    if cust_rows:
        c_img = _hbar(
            [r["customer"] for r in reversed(cust_rows)],
            [r["count"] for r in reversed(cust_rows)],
            "Complaints by Customer",
            color=_G,
        )
        c_img = (
            Image(
                c_img._restrictSize(8.5 * cm, 7 * cm),
                width=8.5 * cm,
                height=c_img.drawHeight * (8.5 * cm / c_img.drawWidth),
            )
            if False
            else c_img
        )  # keep original size

    pl_rows = [r for r in pl_plant_data if r.get(plant, 0) > 0]
    pl_img = None
    if pl_rows:
        pl_img = _pie(
            [r["product_line"] for r in pl_rows],
            [r.get(plant, 0) for r in pl_rows],
            f"{plant} — Product Lines",
        )

    tc = _two_col(c_img, pl_img)
    if tc:
        story.append(tc)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 4. CS1 / CS2
    # ────────────────────────────────────────────────────
    story += _section_banner("🏷️  CS1 / CS2 Classification", S)
    cs_rows = [r for r in cs_all if r.get("plant") == plant]
    if cs_rows:
        story.append(_cs_grouped_bar(cs_rows, plant, f"{plant} — CS1 vs CS2 per Month"))
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 5. OPEN vs CLOSED + STATUS DONUT  (two-col)
    # ────────────────────────────────────────────────────
    story += _section_banner("📋  Complaint Status", S)
    oc_rows = [r for r in oc_all if r.get("plant") == plant]
    oc_img = None
    if oc_rows:
        oc_img = _open_closed_area(oc_rows, f"{plant} — Open vs Closed")

    # status donut from status_monthly aggregated
    status_totals: Dict[str, int] = {}
    for row in status_monthly:
        for k in (
            "open",
            "in_progress",
            "under_review",
            "resolved",
            "closed",
            "rejected",
        ):
            status_totals[k] = status_totals.get(k, 0) + row.get(k, 0)
    donut_img = _donut(
        list(status_totals.keys()), list(status_totals.values()), "Overall Status Mix"
    )
    tc2 = _two_col(oc_img, donut_img)
    if tc2:
        story.append(tc2)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 6. VALEO TREND
    # ────────────────────────────────────────────────────
    if valeo_monthly and any(r["count"] for r in valeo_monthly):
        story += _section_banner("🔑  Valeo Complaints Trend", S)
        story.append(_valeo_line(valeo_monthly, f"Valeo Complaints per Month — {year}"))
        story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 7. DEFECT TYPES
    # ────────────────────────────────────────────────────
    if defect_types:
        story += _section_banner("🔍  Defect Types", S)
        d_img = _defect_bar(defect_types, f"{plant} — Defect Type Ranking")
        if d_img:
            story.append(d_img)
        story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 8. REPETITIVE COMPLAINTS
    # ────────────────────────────────────────────────────
    story += _section_banner("🔁  Repetitive Complaints", S)
    rep_rows = [r for r in rep_by_plant if r.get("plant") == plant]
    rep_img = None
    if rep_rows:
        rep_img = _pie(
            [r["repetition_number"] for r in rep_rows],
            [r["count"] for r in rep_rows],
            f"{plant} — Repetition Distribution",
            center_text=str(sum(r["count"] for r in rep_rows)),
        )
    # repetition table
    rep_tbl_data = None
    if rep_rows:
        hdr = [Paragraph(h, S["th"]) for h in ["Category", "Count", "% of Plant"]]
        total_rep = sum(r["count"] for r in rep_rows)
        rows_t = [hdr] + [
            [
                Paragraph(r["repetition_number"], S["td"]),
                Paragraph(str(r["count"]), S["td"]),
                Paragraph(
                    f'{r["count"]/total_rep*100:.1f}%' if total_rep else "—", S["td"]
                ),
            ]
            for r in rep_rows
        ]
        half = (PAGE_W - 24 * mm) / 2
        rep_tbl = Table(rows_t, colWidths=[half * 0.5, half * 0.25, half * 0.25])
        rep_tbl.setStyle(_tbl_style())
        rep_tbl_data = rep_tbl

    tc3 = _two_col(rep_img, rep_tbl_data)
    if tc3:
        story.append(tc3)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 9. D-STEP COSTS
    # ────────────────────────────────────────────────────
    cost_img = _cost_bar(
        cost_by_step, plant=plant, title=f"{plant} — 8D Cost by Step (€)"
    )
    if cost_img:
        story += _section_banner("💶  8D Cost by D-Step", S)
        story.append(cost_img)
        story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # 10. OVERDUE STEPS TABLE
    # ────────────────────────────────────────────────────
    plant_od_steps = [r for r in overdue_steps if r.get("plant") == plant]
    if plant_od_steps:
        story += _section_banner("⚠️  Overdue 8D Steps", S)
        hdr = [Paragraph(h, S["th"]) for h in ["Step", "Overdue Count"]]
        rows_t = [hdr] + [
            [Paragraph(r["step"], S["td"]), Paragraph(str(r["count"]), S["td"])]
            for r in plant_od_steps
        ]
        half = (PAGE_W - 24 * mm) / 2
        tbl = Table(rows_t, colWidths=[half, half])
        tbl.setStyle(_tbl_style())
        story.append(tbl)
        story.append(Spacer(1, 3 * mm))

    # ────────────────────────────────────────────────────
    # 11. CQT ENGINEER LATENESS TABLE
    # ────────────────────────────────────────────────────
    if cqt_by_cqt:
        story += _section_banner("👤  CQT Engineer Lateness", S)
        hdr = [
            Paragraph(h, S["th"])
            for h in ["CQT Engineer", "Late Complaints", "Overdue Steps"]
        ]
        rows_t = [hdr] + [
            [
                Paragraph(r["cqt_email"], S["td_l"]),
                Paragraph(str(r["late_complaints"]), S["td"]),
                Paragraph(str(r["total_steps_overdue"]), S["td"]),
            ]
            for r in cqt_by_cqt[:20]
        ]
        w = PAGE_W - 24 * mm
        tbl = Table(rows_t, colWidths=[w * 0.55, w * 0.225, w * 0.225])
        tbl.setStyle(_tbl_style())
        story.append(tbl)

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# CONSOLIDATED  (Quality Manager) REPORT
# ─────────────────────────────────────────────────────────────────────────────


def consolidated_report(data: Dict[str, Any], month: int, year: int) -> bytes:
    S = _S()
    buf = io.BytesIO()
    doc = _build_doc(
        buf, "AVOCarbon  ·  Quality KPI Report  ·  All Plants", month, year
    )
    story: list = []
    m_name = MONTH_SHORT[month - 1]
    m_long = MONTH_LONG[month - 1]

    # ── All data references ──
    monthly_data = data.get("monthly_data", [])
    monthly_targets = data.get("monthly_targets", {})
    total_by_plant = data.get("total_by_plant", [])
    od_complaints = data.get("overdue_complaints", {})
    od_by_plant = od_complaints.get("by_plant", [])
    cqt_data = data.get("cqt_lateness", {})
    quarterly = data.get("quarterly_by_plant", [])
    cs_all = data.get("cs_type_per_plant_monthly", [])
    oc_all = data.get("open_closed_per_plant_monthly", [])
    status_monthly = data.get("status_monthly", [])
    rep_dist = data.get("repetitive_distribution", [])
    rep_by_plant = data.get("repetitive_by_plant", [])
    defect_types = data.get("defect_types", [])
    product_types = data.get("product_types", [])
    valeo_monthly = data.get("valeo_monthly", [])
    cost_by_step = data.get("cost_by_step_plant", [])
    pl_plant_data = data.get("complaints_by_product_line_plant", [])
    cust_plant_data = data.get("complaints_by_customer_plant", [])
    cust_avocarbon = data.get("complaints_per_customer_avocarbon", [])
    report_stats = data.get("report_stats", {})
    monthly_vs_target = data.get("monthly_vs_target", [])
    overdue_steps_all = data.get("overdue_steps", [])

    plants = sorted({r["plant"] for r in total_by_plant if r.get("plant")})
    all_plant_keys = [
        k
        for k in (monthly_data[0] if monthly_data else {})
        if k not in ("month", "total")
    ]

    # ── GROUP KPIs ──
    story.append(Spacer(1, 3 * mm))
    story.append(
        _kpi_cards(
            [
                {
                    "label": f"Total Complaints YTD",
                    "value": data.get("total_complaints", 0),
                    "delta": "",
                },
                {
                    "label": "Top Plant",
                    "value": data.get("top_plant", {}).get("plant", "—"),
                    "delta": f'{data.get("top_plant",{}).get("count",0)} complaints',
                },
                {
                    "label": "Overdue Complaints",
                    "value": od_complaints.get("total", 0),
                    "delta": "",
                },
                {
                    "label": "CQT Late",
                    "value": cqt_data.get("total_late", 0),
                    "delta": "",
                },
                {
                    "label": "8D Reports",
                    "value": report_stats.get("total_reports", 0),
                    "delta": "",
                },
            ]
        )
    )
    story.append(Spacer(1, 5 * mm))

    # ────────────────────────────────────────────────────
    # A. MONTHLY ALL-PLANT STACKED BAR
    # ────────────────────────────────────────────────────
    story += _section_banner("📈  Monthly Overview — All Plants", S)
    story.append(
        _bar_monthly(monthly_data, title=f"All Plants — Monthly Complaints {year}")
    )
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # B. QUARTERLY TABLE + PIE  (two-col)
    # ────────────────────────────────────────────────────
    story += _section_banner("📊  Quarterly Breakdown & Plant Distribution", S)
    q_img = _quarterly_grouped(quarterly, title="Complaints by Quarter")
    dist_img = _pie(
        [r["plant"] for r in total_by_plant],
        [r["count"] for r in total_by_plant],
        "Total by Plant (YTD)",
    )
    tc = _two_col(q_img, dist_img)
    if tc:
        story.append(tc)
    story.append(Spacer(1, 3 * mm))

    # Quarterly table
    if quarterly:
        plant_cols = [k for k in quarterly[0] if k not in ("quarter", "total")]
        hdr = [Paragraph(h, S["th"]) for h in ["Quarter"] + plant_cols + ["Total"]]
        rows_t = [hdr]
        for q in quarterly:
            row = [Paragraph(q["quarter"], S["td"])]
            row += [Paragraph(str(q.get(p, 0)), S["td"]) for p in plant_cols]
            row += [Paragraph(str(q["total"]), S["td"])]
            rows_t.append(row)
        total_row = [Paragraph("TOTAL", S["th"])]
        for p in plant_cols:
            total_row.append(
                Paragraph(str(sum(q.get(p, 0) for q in quarterly)), S["td"])
            )
        total_row.append(Paragraph(str(sum(q["total"] for q in quarterly)), S["td"]))
        rows_t.append(total_row)
        n = len(plant_cols) + 2
        tbl = Table(rows_t, colWidths=[(PAGE_W - 24 * mm) / n] * n)
        tbl.setStyle(_tbl_style(has_total=True))
        story.append(tbl)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # C. CUSTOMER × PLANT HEATMAP
    # ────────────────────────────────────────────────────
    story += _section_banner("🗺️  Customer × Plant Heatmap (Top 15 Customers)", S)
    hm = _heatmap_customer_plant(cust_plant_data, all_plant_keys)
    if hm:
        story.append(hm)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # D. TOP 10 CUSTOMERS  +  PRODUCT LINE STACKED
    # ────────────────────────────────────────────────────
    story += _section_banner("👥  Top Customers & Product Lines", S)
    top10_cust = sorted(cust_plant_data, key=lambda x: x.get("total", 0), reverse=True)[
        :10
    ]
    c_img = (
        _hbar(
            [r["customer"] for r in reversed(top10_cust)],
            [r["total"] for r in reversed(top10_cust)],
            "Top 10 Customers (Group Total)",
            color=_G,
        )
        if top10_cust
        else None
    )
    pl_img = _product_line_stacked(pl_plant_data, all_plant_keys)
    tc = _two_col(c_img, None)
    if c_img:
        story.append(c_img)
    story.append(Spacer(1, 3 * mm))
    if pl_img:
        story.append(pl_img)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # E. CS1 / CS2  (all-plant grouped bar  –  choose top 3 plants)
    # ────────────────────────────────────────────────────
    story += _section_banner("🏷️  CS1 / CS2 by Plant", S)
    # aggregate CS per plant across all months
    cs_agg: Dict[str, Dict[str, int]] = {}
    for r in cs_all:
        p = r["plant"]
        cs_agg.setdefault(p, {"CS1": 0, "CS2": 0})
        cs_agg[p]["CS1"] += r["CS1"]
        cs_agg[p]["CS2"] += r["CS2"]
    cs_plants = sorted(
        cs_agg, key=lambda p: cs_agg[p]["CS1"] + cs_agg[p]["CS2"], reverse=True
    )[:8]
    if cs_plants:
        fig, ax = plt.subplots(figsize=(11, 3.8))
        fig.patch.set_alpha(0)
        _apply_avo_style(ax)
        x = np.arange(len(cs_plants))
        w = 0.38
        ax.bar(
            x - w / 2,
            [cs_agg[p]["CS1"] for p in cs_plants],
            w,
            label="CS1",
            color=_G,
            alpha=0.87,
            edgecolor=_N,
            lw=0.3,
        )
        ax.bar(
            x + w / 2,
            [cs_agg[p]["CS2"] for p in cs_plants],
            w,
            label="CS2",
            color=_AU,
            alpha=0.87,
            edgecolor=_N,
            lw=0.3,
        )
        ax.set_xticks(x)
        ax.set_xticklabels(cs_plants, fontsize=7)
        ax.legend(fontsize=7, framealpha=0.9)
        ax.set_title(
            "CS1 vs CS2 per Plant (YTD)", fontsize=9, fontweight="bold", color=_N
        )
        fig.tight_layout(pad=0.5)
        story.append(_fig_to_img(fig, 16, 5))
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # F. OPEN vs CLOSED + STATUS DONUT
    # ────────────────────────────────────────────────────
    story += _section_banner("📋  Complaint Status — Group", S)
    # Aggregate open/closed across all plants
    oc_group: Dict[str, Dict[str, int]] = {}
    for r in oc_all:
        m = r["month"]
        oc_group.setdefault(m, {"open": 0, "closed": 0})
        oc_group[m]["open"] += r["open"]
        oc_group[m]["closed"] += r["closed"]
    oc_months_sorted = [m for m in MONTH_SHORT if m in oc_group]
    if oc_months_sorted:
        oc_rows_grp = [
            {"month": m, "open": oc_group[m]["open"], "closed": oc_group[m]["closed"]}
            for m in oc_months_sorted
        ]
        story.append(_open_closed_area(oc_rows_grp, "Group — Open vs Closed per Month"))
    story.append(Spacer(1, 3 * mm))

    status_totals: Dict[str, int] = {}
    for row in status_monthly:
        for k in (
            "open",
            "in_progress",
            "under_review",
            "resolved",
            "closed",
            "rejected",
        ):
            status_totals[k] = status_totals.get(k, 0) + row.get(k, 0)
    d_img = _donut(
        list(status_totals.keys()),
        list(status_totals.values()),
        "Overall Complaint Status",
    )
    rep_d = _pie(
        [r["label"] for r in rep_dist],
        [r["count"] for r in rep_dist],
        "Repetition Distribution (Group)",
    )
    tc = _two_col(d_img, rep_d)
    if tc:
        story.append(tc)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # G. VALEO TREND
    # ────────────────────────────────────────────────────
    if valeo_monthly and any(r["count"] for r in valeo_monthly):
        story += _section_banner("🔑  Valeo Complaints Trend", S)
        story.append(_valeo_line(valeo_monthly, f"Valeo Complaints per Month — {year}"))
        story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # H. DEFECT TYPES + PRODUCT TYPES  (two-col)
    # ────────────────────────────────────────────────────
    story += _section_banner("🔍  Defect & Product Type Analysis", S)
    def_img = _defect_bar(defect_types, "Top Defect Types (Group)")
    prod_img = (
        _pie(
            [r["type"] for r in product_types[:10]],
            [r["count"] for r in product_types[:10]],
            "Product Types",
        )
        if product_types
        else None
    )
    tc = _two_col(def_img, prod_img)
    if tc:
        story.append(tc)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # I. OVERDUE — by plant + by CQT
    # ────────────────────────────────────────────────────
    story += _section_banner("⚠️  Overdue Complaints & CQT Lateness", S)
    od_img = (
        _hbar(
            [r["plant"] for r in reversed(od_by_plant)],
            [r["count"] for r in reversed(od_by_plant)],
            "Overdue Complaints by Plant",
            color=_RE,
        )
        if od_by_plant
        else None
    )
    od_step_agg: Dict[str, int] = {}
    for r in overdue_steps_all:
        od_step_agg[r["step"]] = od_step_agg.get(r["step"], 0) + r["count"]
    od_step_img = (
        _hbar(
            list(od_step_agg.keys()),
            list(od_step_agg.values()),
            "Overdue by 8D Step (Group)",
            color=_OR,
        )
        if od_step_agg
        else None
    )
    tc = _two_col(od_img, od_step_img)
    if tc:
        story.append(tc)
    story.append(Spacer(1, 3 * mm))

    # CQT table
    cqt_rows = cqt_data.get("by_cqt", [])
    if cqt_rows:
        hdr = [
            Paragraph(h, S["th"])
            for h in ["CQT Engineer", "Late Complaints", "Overdue Steps"]
        ]
        rows_t = [hdr] + [
            [
                Paragraph(r["cqt_email"], S["td_l"]),
                Paragraph(str(r["late_complaints"]), S["td"]),
                Paragraph(str(r["total_steps_overdue"]), S["td"]),
            ]
            for r in cqt_rows[:15]
        ]
        w = PAGE_W - 24 * mm
        tbl = Table(rows_t, colWidths=[w * 0.55, w * 0.225, w * 0.225])
        tbl.setStyle(_tbl_style())
        story.append(tbl)
    story.append(Spacer(1, 4 * mm))

    # ────────────────────────────────────────────────────
    # J. D-STEP COST  +  8D COMPLETION TABLE
    # ────────────────────────────────────────────────────
    story += _section_banner("💶  8D Report & Cost Analysis", S)
    cost_img = _cost_bar(cost_by_step, title="8D Cost by Plant & Step (€)")
    if cost_img:
        story.append(cost_img)
    story.append(Spacer(1, 3 * mm))

    step_comp = report_stats.get("step_completion", [])
    if step_comp:
        hdr = [Paragraph(h, S["th"]) for h in ["Step", "Completed", "Total", "Rate %"]]
        rows_t = [hdr]
        for sc in step_comp:
            rate = sc["completion_rate"]
            rate_c = _G if rate >= 80 else (_AU if rate >= 50 else _RE)
            rows_t.append(
                [
                    Paragraph(sc["step"], S["td"]),
                    Paragraph(str(sc["completed"]), S["td"]),
                    Paragraph(str(sc["total"]), S["td"]),
                    Paragraph(f'<font color="{rate_c}"><b>{rate}%</b></font>', S["td"]),
                ]
            )
        w = PAGE_W - 24 * mm
        tbl = Table(rows_t, colWidths=[w * 0.15, w * 0.28, w * 0.28, w * 0.29])
        tbl.setStyle(_tbl_style())
        story.append(tbl)
    story.append(Spacer(1, 4 * mm))

    # ════════════════════════════════════════════════════
    # PER-PLANT SECTIONS  (one page-break per plant)
    # ════════════════════════════════════════════════════
    for plant in plants:
        story.append(PageBreak())

        # Plant header banner
        story.append(
            Table(
                [
                    [
                        Paragraph(
                            f"  {plant}  —  Plant Detail Report",
                            ParagraphStyle(
                                "PH",
                                fontName="Helvetica-Bold",
                                fontSize=14,
                                textColor=AVO_WHITE,
                                alignment=TA_LEFT,
                            ),
                        )
                    ]
                ],
                colWidths=[PAGE_W - 24 * mm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), AVO_NAVY),
                        ("TOPPADDING", (0, 0), (-1, -1), 8),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                        ("LEFTPADDING", (0, 0), (-1, -1), 10),
                        ("ROUNDEDCORNERS", (0, 0), (-1, -1), 4),
                    ]
                ),
            )
        )
        story.append(Spacer(1, 3 * mm))

        # Mini KPI row
        p_m_count = next(
            (r.get(plant, 0) for r in monthly_data if r["month"] == m_name), 0
        )
        p_target = _compute_target(data, plant)
        p_target_label = (
            f"{p_target:.1f}  (−30 % last-yr avg)"
            if data.get("last_year_by_plant")
            else str(int(p_target))
        )
        p_ytd = sum(r.get(plant, 0) for r in monthly_data)
        p_overdue = next((r["count"] for r in od_by_plant if r["plant"] == plant), 0)
        p_late = next(
            (
                r["late_complaints"]
                for r in cqt_data.get("by_plant", [])
                if r["plant"] == plant
            ),
            0,
        )

        story.append(
            _kpi_cards(
                [
                    {"label": f"{m_long} Complaints", "value": p_m_count, "delta": ""},
                    {"label": "Monthly Target", "value": p_target_label, "delta": ""},
                    {"label": f"YTD {year}", "value": p_ytd, "delta": ""},
                    {"label": "Overdue", "value": p_overdue, "delta": ""},
                    {"label": "CQT Late", "value": p_late, "delta": ""},
                ]
            )
        )
        story.append(Spacer(1, 3 * mm))

        # Monthly trend
        story.append(
            _bar_monthly(
                monthly_data,
                plant=plant,
                title=f"{plant} — Monthly Trend {year}",
                target=p_target,
            )
        )
        story.append(Spacer(1, 3 * mm))

        # Customers + Product line
        c_rows = sorted(
            [r for r in cust_avocarbon if r.get("avocarbon_plant") == plant],
            key=lambda x: x["count"],
            reverse=True,
        )[:8]
        pl_rows2 = [r for r in pl_plant_data if r.get(plant, 0) > 0]
        c2 = (
            _hbar(
                [r["customer"] for r in reversed(c_rows)],
                [r["count"] for r in reversed(c_rows)],
                f"{plant} — Customers",
            )
            if c_rows
            else None
        )
        pl2 = (
            _pie(
                [r["product_line"] for r in pl_rows2],
                [r.get(plant, 0) for r in pl_rows2],
                f"{plant} — Product Lines",
            )
            if pl_rows2
            else None
        )
        tc = _two_col(c2, pl2)
        if tc:
            story.append(tc)
        story.append(Spacer(1, 3 * mm))

        # CS1/CS2 + open/closed
        cs_p = [r for r in cs_all if r.get("plant") == plant]
        oc_p = [r for r in oc_all if r.get("plant") == plant]
        cs_img2 = (
            _cs_grouped_bar(cs_p, plant, f"{plant} — CS1 vs CS2") if cs_p else None
        )
        oc_img2 = _open_closed_area(oc_p, f"{plant} — Open vs Closed") if oc_p else None
        tc = _two_col(cs_img2, oc_img2)
        if tc:
            story.append(tc)

    doc.build(story)
    return buf.getvalue()
