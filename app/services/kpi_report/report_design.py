"""
app/services/reports/report_design.py
───────────────────────────────────────
Owner: Design / layout collaborator
Responsibility:
  - Brand palette & typography
  - All Matplotlib chart functions
  - All ReportLab table builders
  - Page header/footer & document template
  - Section banners, KPI cards, layout utilities

No business logic or AI calls live here.
Data arrives ready-to-render from report_builder.py.
"""

from __future__ import annotations

import io
import logging
import os
import textwrap
import urllib.request
from typing import  Dict, List, Optional

import matplotlib
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from datetime import date

matplotlib.use("Agg")

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.autolayout": False,
    "savefig.dpi": 200,
})

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from .kpi_logic import MONTH_SHORT, MONTH_LONG

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Brand Palette
# ─────────────────────────────────────────────────────────────────────────────
C_INK        = colors.HexColor("#0A0F1E")
C_NAVY       = colors.HexColor("#0D1B2A")
C_NAVY_MID   = colors.HexColor("#152236")
C_COBALT     = colors.HexColor("#1C4E80")
C_ELECTRIC   = colors.HexColor("#0EA5E9")
C_ICE        = colors.HexColor("#BAE6FD")
C_FROST      = colors.HexColor("#F0F9FF")
C_GOLD       = colors.HexColor("#F0A500")
C_GOLD_PALE  = colors.HexColor("#FEF3C7")
C_SLATE      = colors.HexColor("#475569")
C_MIST       = colors.HexColor("#CBD5E1")
C_PAPER      = colors.HexColor("#F8FAFC")
C_WHITE      = colors.white
C_RED        = colors.HexColor("#EF4444")
C_RED_PALE   = colors.HexColor("#FEE2E2")
C_AMBER      = colors.HexColor("#F59E0B")
C_AMBER_PALE = colors.HexColor("#FEF3C7")
C_GREEN      = colors.HexColor("#10B981")
C_GREEN_PALE = colors.HexColor("#D1FAE5")
C_PURPLE     = colors.HexColor("#8B5CF6")
C_TEAL       = colors.HexColor("#14B8A6")
C_ROSE       = colors.HexColor("#F43F5E")

# Matplotlib equivalents
_INK = "#0A0F1E"; _NAV = "#0D1B2A"; _ELE = "#0EA5E9"; _GLD = "#F0A500"
_SL  = "#475569"; _RED = "#EF4444"; _AMB = "#F59E0B"; _GRN = "#10B981"
_PUR = "#8B5CF6"; _TEA = "#14B8A6"; _ROS = "#F43F5E"; _ORG = "#F97316"

PALETTE = [_ELE, _TEA, _PUR, _ORG, _GLD, _GRN, _RED, _ROS,
           "#22D3EE", "#A78BFA", "#34D399", "#FCD34D"]

# ─────────────────────────────────────────────────────────────────────────────
# Dimensions
# ─────────────────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
_MARGIN_L    = 14 * mm
_MARGIN_R    = 14 * mm
_BODY_W      = PAGE_W - _MARGIN_L - _MARGIN_R
_AI_COL_W    = 72 * mm
_CHART_COL_W = _BODY_W - _AI_COL_W - 5 * mm
_COL_W       = (_BODY_W - 8) / 2

# ─────────────────────────────────────────────────────────────────────────────
# Chart theme
# ─────────────────────────────────────────────────────────────────────────────
_BG_CHART  = "#DCDCDC"
_TC_TITLE  = "#0A0F1E"
_TC_AXIS   = "#1C2E3F"
_TC_LABEL  = "#0A0F1E"
_TC_LEGEND = "#0A0F1E"
_TC_GRID   = "#AAAAAA"
_TC_SPINE  = "#334155"
_TC_TARGET = "#B45309"

# ─────────────────────────────────────────────────────────────────────────────
# Logo
# ─────────────────────────────────────────────────────────────────────────────
_LOGO_URL  = "https://avocarbon-customer-complaint.azurewebsites.net/assets/logo-avocarbon-BPLJ2lDY.png"
_LOGO_PATH = "/tmp/_avo_logo_cached.png"
_logo_bytes: Optional[bytes] = None


def get_logo() -> Optional[bytes]:
    global _logo_bytes
    if _logo_bytes is not None:
        return _logo_bytes
    if os.path.exists(_LOGO_PATH):
        with open(_LOGO_PATH, "rb") as f:
            _logo_bytes = f.read()
        return _logo_bytes
    try:
        req = urllib.request.Request(_LOGO_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            _logo_bytes = resp.read()
        with open(_LOGO_PATH, "wb") as f:
            f.write(_logo_bytes)
        return _logo_bytes
    except Exception as exc:
        logger.warning("Logo fetch failed (%s) — text fallback", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Typography
# ─────────────────────────────────────────────────────────────────────────────
def build_styles() -> Dict[str, ParagraphStyle]:
    """Return the full typography system. Called once per report build."""
    return {
        "h1": ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=13,
                              textColor=C_NAVY, spaceBefore=8, spaceAfter=5, leading=17),
        "h2": ParagraphStyle("H2", fontName="Helvetica-Bold", fontSize=10,
                              textColor=C_COBALT, spaceBefore=6, spaceAfter=3, leading=14),
        "h3": ParagraphStyle("H3", fontName="Helvetica-Bold", fontSize=8.5,
                              textColor=C_NAVY, spaceBefore=4, spaceAfter=2, leading=12),
        "body": ParagraphStyle("Body", fontName="Helvetica", fontSize=7.5,
                               textColor=C_INK, leading=11.5),
        "body_sm": ParagraphStyle("BodySm", fontName="Helvetica", fontSize=7,
                                  textColor=C_SLATE, leading=10.5),
        "caption": ParagraphStyle("Cap", fontName="Helvetica-Oblique", fontSize=6.5,
                                  textColor=C_SLATE, leading=9.5),
        "kpi_val": ParagraphStyle("KV", fontName="Helvetica-Bold", fontSize=24,
                                  textColor=C_NAVY, alignment=TA_CENTER, leading=28),
        "kpi_sub": ParagraphStyle("KS", fontName="Helvetica", fontSize=7,
                                  textColor=C_ELECTRIC, alignment=TA_CENTER, leading=9),
        "kpi_lbl": ParagraphStyle("KL", fontName="Helvetica", fontSize=6.5,
                                  textColor=C_SLATE, alignment=TA_CENTER, leading=9),
        "kpi_delta_good": ParagraphStyle("KDG", fontName="Helvetica-Bold", fontSize=6.5,
                                         textColor=C_GREEN, alignment=TA_CENTER, leading=9),
        "kpi_delta_bad": ParagraphStyle("KDB", fontName="Helvetica-Bold", fontSize=6.5,
                                        textColor=C_RED, alignment=TA_CENTER, leading=9),
        "kpi_delta_neu": ParagraphStyle("KDN", fontName="Helvetica", fontSize=6.5,
                                        textColor=C_SLATE, alignment=TA_CENTER, leading=9),
        "th": ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=6.8,
                             textColor=C_WHITE, alignment=TA_CENTER),
        "td": ParagraphStyle("TD", fontName="Helvetica", fontSize=6.8,
                             textColor=C_INK, alignment=TA_CENTER),
        "td_l": ParagraphStyle("TDL", fontName="Helvetica", fontSize=6.8,
                               textColor=C_INK, alignment=TA_LEFT),
        "td_r": ParagraphStyle("TDR", fontName="Helvetica", fontSize=6.8,
                               textColor=C_INK, alignment=TA_RIGHT),
        "td_bold": ParagraphStyle("TDB", fontName="Helvetica-Bold", fontSize=6.8,
                                  textColor=C_COBALT, alignment=TA_CENTER),
        "ai_head": ParagraphStyle("AIH", fontName="Helvetica-Bold", fontSize=7.5,
                                  textColor=C_NAVY, leading=10, spaceAfter=2),
        "ai_body": ParagraphStyle("AIB", fontName="Helvetica", fontSize=6.8,
                                  textColor=C_INK, leading=9.8),
        "ai_label": ParagraphStyle("AIL", fontName="Helvetica-Bold", fontSize=6,
                                   textColor=C_ELECTRIC, leading=8.5, spaceBefore=4),
        "ai_action": ParagraphStyle("AIA", fontName="Helvetica", fontSize=6.5,
                                    textColor=C_INK, leading=9.5, leftIndent=5),
        "cover_kicker": ParagraphStyle("CK", fontName="Helvetica", fontSize=9,
                                       textColor=C_ELECTRIC, alignment=TA_CENTER,
                                       letterSpacing=2, spaceAfter=4),
        "cover_title": ParagraphStyle("CT", fontName="Helvetica-Bold", fontSize=32,
                                      textColor=C_WHITE, alignment=TA_CENTER, spaceAfter=8,
                                      leading=38),
        "cover_plant": ParagraphStyle("CP", fontName="Helvetica-Bold", fontSize=22,
                                      textColor=C_GOLD, alignment=TA_CENTER, spaceAfter=6),
        "cover_meta": ParagraphStyle("CM", fontName="Helvetica", fontSize=9,
                                     textColor=C_ICE, alignment=TA_CENTER),
        "footer": ParagraphStyle("Ft", fontName="Helvetica", fontSize=5.8,
                                 textColor=C_SLATE, alignment=TA_CENTER),
        "note": ParagraphStyle("Note", fontName="Helvetica-Oblique", fontSize=6.5,
                               textColor=C_SLATE, leading=9),
        "alert": ParagraphStyle("Alert", fontName="Helvetica-Bold", fontSize=7.5,
                                textColor=C_RED),
        "section_sm": ParagraphStyle("SecS", fontName="Helvetica-Bold", fontSize=8.5,
                                     textColor=C_COBALT, spaceBefore=6, spaceAfter=2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Table style factory
# ─────────────────────────────────────────────────────────────────────────────
def table_style(has_total: bool = False, accent_col: int = -1) -> TableStyle:
    cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_NAVY),
        ("TEXTCOLOR",     (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 6.8),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("LINEBELOW",     (0, 0), (-1, 0), 2, C_ELECTRIC),
        ("LINEBELOW",     (0, 1), (-1, -2), 0.15, C_MIST),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_PAPER]),
    ]
    if has_total:
        cmds += [
            ("BACKGROUND", (0, -1), (-1, -1), C_ICE),
            ("FONTNAME",   (0, -1), (-1, -1), "Helvetica-Bold"),
            ("TEXTCOLOR",  (0, -1), (-1, -1), C_NAVY),
            ("LINEABOVE",  (0, -1), (-1, -1), 0.8, C_COBALT),
        ]
    if accent_col >= 0:
        cmds += [
            ("FONTNAME",  (accent_col, 1), (accent_col, -1), "Helvetica-Bold"),
            ("TEXTCOLOR", (accent_col, 1), (accent_col, -1), C_COBALT),
        ]
    return TableStyle(cmds)


# ─────────────────────────────────────────────────────────────────────────────
# 3-D chart primitives
# ─────────────────────────────────────────────────────────────────────────────

def _make_3d_bar(ax, x, height, width=0.65, color=_ELE, depth_frac=0.18,
                 bottom=0, alpha=0.95, zorder=3):
    dx = width * depth_frac
    dy = min(height * depth_frac, height * 0.22) if height > 0 else 0
    base = np.array(mcolors.to_rgb(color))
    ax.add_patch(plt.Rectangle((x - width / 2, bottom), width, height,
                                color=color, alpha=alpha, zorder=zorder, linewidth=0))
    if height > 0:
        ax.fill([x-width/2, x+width/2, x+width/2+dx, x-width/2+dx],
                [bottom+height]*2 + [bottom+height+dy]*2,
                color=mcolors.to_hex(np.clip(base*1.28, 0, 1)),
                alpha=alpha, zorder=zorder+1, linewidth=0)
        ax.fill([x+width/2, x+width/2+dx, x+width/2+dx, x+width/2],
                [bottom, bottom+dy, bottom+height+dy, bottom+height],
                color=mcolors.to_hex(np.clip(base*0.58, 0, 1)),
                alpha=alpha, zorder=zorder, linewidth=0)


def _make_3d_bar_h(ax, y, width, height=0.55, color=_ELE, depth_frac=0.15,
                   left=0, alpha=0.95, zorder=3):
    dx = width * depth_frac * 0.25
    dy = height * depth_frac * 1.8
    base = np.array(mcolors.to_rgb(color))
    ax.add_patch(plt.Rectangle((left, y - height / 2), width, height,
                                color=color, alpha=alpha, zorder=zorder, linewidth=0))
    if width > 0:
        ax.fill([left, left+width, left+width+dx, left+dx],
                [y+height/2]*2 + [y+height/2+dy]*2,
                color=mcolors.to_hex(np.clip(base*1.28, 0, 1)),
                alpha=alpha, zorder=zorder+1, linewidth=0)
        ax.fill([left+width, left+width+dx, left+width+dx, left+width],
                [y-height/2, y-height/2+dy, y+height/2+dy, y+height/2],
                color=mcolors.to_hex(np.clip(base*0.62, 0, 1)),
                alpha=alpha, zorder=zorder, linewidth=0)


def _chart_bg(fig, ax, title="", ylabel=""):
    fig.patch.set_facecolor(_BG_CHART)
    ax.set_facecolor(_BG_CHART)
    ax.yaxis.grid(True, linestyle="--", alpha=0.45, color=_TC_GRID, linewidth=0.7, zorder=0)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)
    for sp in ["top", "right", "left"]:
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(_TC_SPINE)
    ax.spines["bottom"].set_linewidth(1.0)
    ax.tick_params(axis="both", labelsize=8.5, colors=_TC_AXIS, length=0)
    if title:
        ax.set_title(title, fontsize=11, fontweight="bold", color=_TC_TITLE,
                     pad=10, loc="left")
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9, color=_TC_AXIS, labelpad=6)


def _glow_line(ax, x, y, color, label="", lw=2.5, ms=6, ls="-"):
    ax.plot(x, y, color="#00000030", lw=lw+3, alpha=0.25, zorder=2, linestyle=ls)
    ax.plot(x, y, color=color, marker="o", lw=lw, ms=ms, label=label, zorder=4,
            solid_capstyle="round", linestyle=ls,
            markerfacecolor=color, markeredgecolor="white", markeredgewidth=1.0)


def _fig_to_img(fig, w_cm: float = 16, h_cm: float = 6) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight",
                facecolor=fig.get_facecolor(), transparent=False)
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=w_cm * cm, height=h_cm * cm)


def _luma(hex_color: str) -> float:
    r, g, b = mcolors.to_rgb(hex_color)
    return 0.299*r + 0.587*g + 0.114*b


def _gradient_bar_color(base_hex, idx, total, reverse=False):
    base = np.array(mcolors.to_rgb(base_hex))
    t = idx / max(total - 1, 1)
    if reverse:
        t = 1 - t
    return np.clip(base * (0.78 + 0.44 * t), 0, 1)


def _legend_kwargs():
    return dict(fontsize=8, framealpha=0.85, facecolor="#F5F5F5",
                edgecolor="#AAAAAA", labelcolor=_TC_LEGEND, fancybox=False)


def _label_v(ax, rects_or_vals, vals=None, fmt="{v}", fontsize=7.5,
             offset_frac=0.025, color=None):
    col = color or _TC_LABEL
    if vals is None:
        bars = rects_or_vals
        mx = max((b.get_height() for b in bars), default=1) or 1
        for b in bars:
            v = b.get_height()
            ax.text(b.get_x()+b.get_width()/2, v+mx*offset_frac,
                    fmt.format(v=int(v) if float(v)==int(v) else round(v,1)),
                    ha="center", va="bottom", fontsize=fontsize, color=col, fontweight="bold")
    else:
        xs = rects_or_vals
        mx = max(vals, default=1) or 1
        for xi, v in zip(xs, vals):
            ax.text(xi, v+mx*offset_frac,
                    fmt.format(v=int(v) if float(v)==int(v) else round(v,1)),
                    ha="center", va="bottom", fontsize=fontsize, color=col, fontweight="bold")


def _label_h(ax, ys, vals, fmt="{v}", fontsize=7.5, color=None):
    col = color or _TC_LABEL
    mx = max(vals, default=1) or 1
    for yi, v in zip(ys, vals):
        ax.text(v+mx*0.02, yi, fmt.format(v=int(v) if float(v)==int(v) else round(v,1)),
                va="center", fontsize=fontsize, color=col, fontweight="bold")


def _label_stacked(ax, x_pos, seg_vals_list, bots_list, colors_list, threshold=0.05):
    totals = np.array([sum(sv[i] for sv in seg_vals_list) for i in range(len(x_pos))], dtype=float)
    for seg_vals, bot, col in zip(seg_vals_list, bots_list, colors_list):
        for xi, (v, b, tot) in enumerate(zip(seg_vals, bot, totals)):
            if not v or (tot and v/tot < threshold):
                continue
            ax.text(x_pos[xi], b+v/2, str(int(v)), ha="center", va="center",
                    fontsize=7, color="white" if _luma(col) < 0.45 else "#0A0F1E",
                    fontweight="bold")


# ─────────────────────────────────────────────────────────────────────────────
# Chart functions
# ─────────────────────────────────────────────────────────────────────────────

def chart_bar_monthly(monthly_data, plant=None, title="", target=None) -> Image:
    months     = [d["month"] for d in monthly_data]
    plant_keys = [k for k in (monthly_data[0] if monthly_data else {})
                  if k not in ("month", "total")]
    fig, ax = plt.subplots(figsize=(12, 4.8))
    _chart_bg(fig, ax, title=title, ylabel="Complaints")

    if plant:
        vals = [d.get(plant, 0) for d in monthly_data]
        for i, v in enumerate(vals):
            _make_3d_bar(ax, i, v, color=_RED if (target and v > target) else _ELE)
        _label_v(ax, list(range(len(months))), vals)
        if target and target > 0:
            ax.axhline(target, color=_TC_TARGET, linestyle="--", linewidth=2.2, zorder=5,
                       label=f"Target {target:.1f}")
            for i, v in enumerate(vals):
                if v > target:
                    ax.fill_between([i-.32, i+.32], [target]*2, [v]*2,
                                    alpha=0.18, color=_RED, zorder=6)
            ax.legend(**_legend_kwargs(), loc="upper right")
        ax.set_xticks(range(len(months)))
        ax.set_xticklabels(months, fontsize=8.5, color=_TC_AXIS)
    else:
        x = np.arange(len(months)); bottoms = np.zeros(len(months))
        sv, sb, sc = [], [], []
        for i, p in enumerate(plant_keys):
            vals = np.array([d.get(p, 0) for d in monthly_data], dtype=float)
            col  = PALETTE[i % len(PALETTE)]
            for xi, (v, bot) in enumerate(zip(vals, bottoms)):
                _make_3d_bar(ax, xi, v, color=col, bottom=bot, depth_frac=0.12)
            ax.bar(x, vals*0, bottom=bottoms, color=col, label=p, alpha=0)
            sv.append(vals); sb.append(bottoms.copy()); sc.append(col); bottoms += vals
        _label_stacked(ax, x, sv, sb, sc)
        totals = [d.get("total", 0) for d in monthly_data]
        mx = max(totals) if totals else 1
        for xi, t in enumerate(totals):
            if t:
                ax.text(xi, t+mx*0.025, str(t), ha="center", va="bottom",
                        fontsize=8.5, color=_TC_LABEL, fontweight="bold")
        ax.set_xticks(x); ax.set_xticklabels(months, fontsize=8.5, color=_TC_AXIS)
        ax.legend(**_legend_kwargs(), loc="upper right", ncol=4)
    ax.set_ylim(bottom=0)
    fig.tight_layout(pad=1.0)
    return _fig_to_img(fig, 16, 5.4)


def chart_pie(labels, values, title) -> Image:
    fig, ax = plt.subplots(figsize=(5.8, 5.2))
    fig.patch.set_facecolor(_BG_CHART); ax.set_facecolor(_BG_CHART)
    non_z = [(l, v) for l, v in zip(labels, values) if v > 0]
    if not non_z:
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color=_TC_TITLE)
        ax.axis("off")
    else:
        ls, vs = zip(*non_z)
        max_i   = vs.index(max(vs))
        explode = [0.04 if i == max_i else 0 for i in range(len(vs))]
        wedges, _, auts = ax.pie(
            vs, colors=PALETTE[:len(ls)], autopct="%1.0f%%", startangle=90,
            pctdistance=0.76, explode=explode,
            wedgeprops=dict(linewidth=2.5, edgecolor=_BG_CHART, antialiased=True),
            textprops=dict(fontsize=7.5, color=_TC_TITLE, fontweight="bold"),
        )
        for at in auts:
            at.set_fontweight("bold"); at.set_color(_TC_TITLE)
        ax.legend(wedges, [f"{l} ({v})" for l, v in zip(ls, vs)],
                  fontsize=6.5, loc="lower center", bbox_to_anchor=(0.5, -0.26),
                  ncol=2, framealpha=0.85, facecolor="#F5F5F5",
                  edgecolor="#AAAAAA", labelcolor=_TC_LEGEND, fancybox=False)
    ax.set_title(title, fontsize=10, fontweight="bold", color=_TC_TITLE, pad=6, loc="center")
    fig.tight_layout(pad=0.6)
    return _fig_to_img(fig, 8.5, 6.8)


def chart_donut(labels, values, title) -> Image:
    fig, ax = plt.subplots(figsize=(5.0, 4.6))
    fig.patch.set_facecolor(_BG_CHART); ax.set_facecolor(_BG_CHART)
    non_z = [(l, v) for l, v in zip(labels, values) if v > 0]
    if not non_z:
        ax.text(0.5, 0.5, "No data", ha="center", va="center",
                transform=ax.transAxes, fontsize=10, color=_TC_TITLE)
        ax.axis("off")
    else:
        ls, vs = zip(*non_z)
        status_colors = {
            "open": _RED, "in_progress": _AMB,
            "resolved": _GRN, "closed": "#334155", "rejected": _SL, "cancelled": "#94A3B8",
        }
        clrs   = [status_colors.get(l.lower().replace(" ","_"), PALETTE[i % len(PALETTE)])
                  for i, l in enumerate(ls)]
        wedges, _ = ax.pie(vs, colors=clrs, startangle=90,
                           wedgeprops=dict(width=0.60, linewidth=3.0,
                                           edgecolor=_BG_CHART, antialiased=True))
        total = sum(vs)
        ax.text(0, 0.09, str(total), ha="center", va="center",
                fontsize=16, fontweight="bold", color=_TC_TITLE)
        ax.text(0, -0.22, "Total", ha="center", va="center", fontsize=8, color=_TC_AXIS)
        ax.legend(wedges, [f"{l} ({v})" for l, v in zip(ls, vs)],
                  fontsize=6.5, loc="lower center", bbox_to_anchor=(0.5, -0.38),
                  ncol=2, framealpha=0.85, facecolor="#F5F5F5",
                  edgecolor="#AAAAAA", labelcolor=_TC_LEGEND, fancybox=False)
    ax.set_title(title, fontsize=10, fontweight="bold", color=_TC_TITLE, pad=6)
    fig.tight_layout(pad=0.5)
    return _fig_to_img(fig, 7.8, 6.0)


def chart_hbar(labels, values, title, color=_ELE, value_prefix="") -> Image:
    n = max(len(labels), 1)
    fig, ax = plt.subplots(figsize=(8.5, max(3.6, n * 0.55 + 1.2)))
    _chart_bg(fig, ax, title=title)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, linestyle="--", alpha=0.45, color=_TC_GRID, linewidth=0.7)
    ax.spines["left"].set_visible(False)
    for i, (lbl, v) in enumerate(zip(labels, values)):
        _make_3d_bar_h(ax, i, v, height=0.55,
                       color=mcolors.to_hex(_gradient_bar_color(color, i, n)))
    ax.set_yticks(list(range(n)))
    ax.set_yticklabels(labels, fontsize=8.5, color=_TC_AXIS)
    ax.set_xlim(0, max(values) * 1.26 if values else 1)
    ax.tick_params(axis="x", labelsize=8.5, colors=_TC_AXIS)
    _label_h(ax, list(range(n)), values, fmt=f"{value_prefix}{{v}}", fontsize=8)
    fig.tight_layout(pad=0.9)
    return _fig_to_img(fig, 10, max(4.4, n * 0.62 + 1.6))


def chart_rolling_claims(rows, plant, report_month, report_year,
                          dyn_target=None) -> Optional[Image]:
    from .kpi_logic import add_months, month_label_short
    plant_rows = [r for r in rows if r.get("plant") == plant]
    if not plant_rows:
        return None
    row_map = {(int(r["year"]), int(r["month_num"])): r
               for r in plant_rows if r.get("year") is not None}
    periods     = [add_months(report_year, report_month, d) for d in range(-11, 4)]
    labels      = [month_label_short(y, m) for y, m in periods]
    actual_vals = [row_map.get((y, m), {}).get("actual", np.nan) for y, m in periods]
    target_vals = [float(row_map.get((y, m), {}).get("target", dyn_target or 0) or 0)
                   for y, m in periods]

    fig, ax = plt.subplots(figsize=(12, 5.0))
    _chart_bg(fig, ax, title=f"{plant} — Claims trend (12 trailing + 3 target months)",
              ylabel="Complaints")
    x = np.arange(len(labels)); hist_cut = 12
    if len(x) > hist_cut:
        ax.axvspan(x[hist_cut]-.5, x[-1]+.5, alpha=0.12, color="#F0A500", zorder=1)
        ax.text((x[hist_cut]+x[-1])/2, 0.97, "Future target months",
                transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=7.5, color="#7C4A00", style="italic", fontweight="bold")
    for i, v in enumerate(actual_vals):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        col = "#7B8FA1" if i >= hist_cut else _ELE
        _make_3d_bar(ax, x[i], v, width=0.68,
                     color=mcolors.to_hex(_gradient_bar_color(col, i % hist_cut, hist_cut)))
    plotted = [0 if (v is None or (isinstance(v, float) and np.isnan(v))) else v
               for v in actual_vals]
    mx = max(plotted) if plotted else 1
    for i, v in enumerate(actual_vals):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        ax.text(x[i], v+mx*0.028,
                f"{int(v) if float(v).is_integer() else v}",
                ha="center", va="bottom", fontsize=8, color=_TC_LABEL, fontweight="bold")
    if any(t > 0 for t in target_vals):
        ax.plot(x, target_vals, color=_TC_TARGET, linestyle="--", linewidth=2.5, zorder=5,
                label=f"Target {(dyn_target or target_vals[0]):.1f}")
    if len(x) > hist_cut:
        ax.axvline(x[hist_cut]-.5, color=_TC_TARGET, linewidth=1.0, linestyle=":", alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8, rotation=32, ha="right", color=_TC_AXIS)
    ax.set_ylim(bottom=0)
    ax.legend(**_legend_kwargs(), loc="upper right")
    fig.tight_layout(pad=1.0)
    return _fig_to_img(fig, 16, 5.8)


def chart_rolling_late_steps(rows, plant, report_month, report_year,
                              dyn_target=None) -> Optional[Image]:
    from .kpi_logic import add_months, month_label_short
    plant_rows = [r for r in rows if r.get("plant") == plant]
    if not plant_rows:
        return None
    row_map     = {(int(r["year"]), int(r["month_num"])): r
                   for r in plant_rows if r.get("year") is not None}
    periods     = [add_months(report_year, report_month, d) for d in range(-11, 4)]
    labels      = [month_label_short(y, m) for y, m in periods]
    actual_vals = [row_map.get((y, m), {}).get("actual", np.nan) for y, m in periods]
    target_vals = [float(row_map.get((y, m), {}).get("target", dyn_target or 0) or 0)
                   for y, m in periods]

    fig, ax = plt.subplots(figsize=(12, 5.0))
    _chart_bg(fig, ax, title=f"{plant} — Late steps trend (12 trailing + 3 target months)",
              ylabel="Late steps")
    x = np.arange(len(labels)); hist_cut = 12
    if len(x) > hist_cut:
        ax.axvspan(x[hist_cut]-.5, x[-1]+.5, alpha=0.12, color="#F0A500", zorder=1)
        ax.text((x[hist_cut]+x[-1])/2, 0.97, "Future target months",
                transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=7.5, color="#7C4A00", style="italic", fontweight="bold")
    for i, v in enumerate(actual_vals):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        col = "#94A3B8" if i >= hist_cut else _RED
        _make_3d_bar(ax, x[i], v, width=0.68,
                     color=mcolors.to_hex(_gradient_bar_color(col, i % hist_cut, hist_cut)))
    plotted = [0 if (v is None or (isinstance(v,float) and np.isnan(v))) else v
               for v in actual_vals]
    mx = max(plotted) if plotted else 1
    for i, v in enumerate(actual_vals):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        ax.text(x[i], v+mx*0.028, f"{int(v) if float(v).is_integer() else v}",
                ha="center", va="bottom", fontsize=8, color=_TC_LABEL, fontweight="bold")
    if any(t > 0 for t in target_vals):
        ax.plot(x, target_vals, color=_TC_TARGET, linestyle="--", linewidth=2.5, zorder=5,
                label=f"Target {(dyn_target or target_vals[0]):.1f}")
        ax.legend(**_legend_kwargs(), loc="upper right")
    if len(x) > hist_cut:
        ax.axvline(x[hist_cut]-.5, color=_TC_TARGET, linewidth=1.0, linestyle=":", alpha=0.7)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8, rotation=32, ha="right", color=_TC_AXIS)
    ax.set_ylim(bottom=0)
    fig.tight_layout(pad=1.0)
    return _fig_to_img(fig, 16, 5.8)


def chart_backlog_rolling(rows, plant, report_month, report_year) -> Optional[Image]:
    from .kpi_logic import add_months, month_label_short
    plant_rows = [r for r in rows if r.get("plant") == plant]
    if not plant_rows:
        return None
    row_map  = {(int(r["year"]), int(r["month_num"])): r
                for r in plant_rows if r.get("year") is not None}
    periods  = [add_months(report_year, report_month, d) for d in range(-11, 4)]
    labels   = [month_label_short(y, m) for y, m in periods]
    vals     = [float(row_map.get((y, m), {}).get("actual", 0) or 0) for y, m in periods]

    fig, ax  = plt.subplots(figsize=(12, 4.8))
    _chart_bg(fig, ax, title=f"{plant} — Backlog trend (15 periods)", ylabel="Open backlog")
    x = np.arange(len(labels)); hist_cut = 12
    if len(x) > hist_cut:
        ax.axvspan(x[hist_cut]-.5, x[-1]+.5, alpha=0.10, color="#F0A500", zorder=1)
        ax.text((x[hist_cut]+x[-1])/2, 0.97, "Future periods",
                transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=7.5, color="#7C4A00", style="italic", fontweight="bold")
    ax.fill_between(x[:hist_cut], vals[:hist_cut], alpha=0.18, color=_ELE, zorder=2)
    _glow_line(ax, x[:hist_cut], vals[:hist_cut], color=_ELE, lw=2.6, ms=5.5)
    if len(x) > hist_cut:
        ax.plot(x[hist_cut-1:], vals[hist_cut-1:], color="#64748B", linestyle="--",
                linewidth=2.0, marker="o", markersize=4.5, zorder=4)
    mx = max(vals) if vals else 1
    for i, v in enumerate(vals):
        ax.text(x[i], v+mx*0.03,
                f"{int(v) if float(v).is_integer() else round(v,1)}",
                ha="center", va="bottom", fontsize=8, color=_TC_LABEL, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8, rotation=32, ha="right", color=_TC_AXIS)
    ax.set_ylim(bottom=0)
    fig.tight_layout(pad=1.0)
    return _fig_to_img(fig, 16, 5.6)


def chart_cs_grouped(cs_rows, plant, title) -> Image:
    months = [r["month"] for r in cs_rows]
    cs1    = [r["CS1"] for r in cs_rows]
    cs2    = [r["CS2"] for r in cs_rows]
    fig, ax = plt.subplots(figsize=(12, 4.2))
    _chart_bg(fig, ax, title=title, ylabel="Complaints")
    x, w = np.arange(len(months)), 0.36
    for i, (v1, v2) in enumerate(zip(cs1, cs2)):
        _make_3d_bar(ax, x[i]-w/2, v1, width=w, color=_ELE, depth_frac=0.14)
        _make_3d_bar(ax, x[i]+w/2, v2, width=w, color=_GLD, depth_frac=0.14)
    ax.bar([0], [0], color=_ELE, label="CS1 Quality", alpha=0)
    ax.bar([0], [0], color=_GLD, label="CS2 Warranty", alpha=0)
    _label_v(ax, [i-w/2 for i in x], cs1, fontsize=7.5)
    _label_v(ax, [i+w/2 for i in x], cs2, fontsize=7.5, color="#7C4A00")
    ax.set_xticks(x); ax.set_xticklabels(months, fontsize=8.5, color=_TC_AXIS)
    ax.set_ylim(bottom=0); ax.legend(**_legend_kwargs())
    fig.tight_layout(pad=0.9)
    return _fig_to_img(fig, 16, 5.0)


def chart_open_closed_area(oc_rows, title) -> Image:
    mo     = list(range(len(oc_rows)))
    labels = [r["month"] for r in oc_rows]
    opens  = [r.get("open", 0) for r in oc_rows]
    closed = [r.get("closed", 0) for r in oc_rows]
    fig, ax = plt.subplots(figsize=(12, 4.2))
    _chart_bg(fig, ax, title=title)
    ax.stackplot(mo, opens, closed, labels=["Open","Closed"],
                 colors=[_RED, _GRN], alpha=0.60, zorder=2)
    ax.plot(mo, np.array(opens), color="#B91C1C", lw=2.0, zorder=3)
    ax.plot(mo, np.array(opens)+np.array(closed), color="#047857", lw=2.0, zorder=3)
    for i, v in enumerate(opens):
        if v:
            ax.text(i, v/2, str(v), ha="center", va="center",
                    fontsize=7.5, color="white", fontweight="bold")
    ax.set_xticks(mo); ax.set_xticklabels(labels, fontsize=8.5, color=_TC_AXIS)
    ax.legend(**_legend_kwargs(), loc="upper right")
    fig.tight_layout(pad=0.9)
    return _fig_to_img(fig, 16, 5.0)


def chart_quarterly_grouped(quarterly, plant=None, title="Quarterly") -> Image:
    plant_keys = [k for k in (quarterly[0] if quarterly else {})
                  if k not in ("quarter","total")]
    quarters   = [q["quarter"] for q in quarterly]
    fig, ax    = plt.subplots(figsize=(8.5, 4.0))
    _chart_bg(fig, ax, title=title, ylabel="Complaints")
    if plant:
        vals = [q.get(plant, 0) for q in quarterly]
        for i, v in enumerate(vals):
            _make_3d_bar(ax, i, v, width=0.62,
                         color=mcolors.to_hex(_gradient_bar_color(PALETTE[i%len(PALETTE)], i, len(vals))))
        _label_v(ax, list(range(len(quarters))), vals)
        ax.set_xticks(range(len(quarters))); ax.set_xticklabels(quarters, fontsize=9, color=_TC_AXIS)
    else:
        x = np.arange(len(quarters)); bottoms = np.zeros(len(quarters))
        sv, sb, sc = [], [], []
        for i, p in enumerate(plant_keys):
            vals = np.array([q.get(p, 0) for q in quarterly])
            col  = PALETTE[i % len(PALETTE)]
            for xi, (v, bot) in enumerate(zip(vals, bottoms)):
                _make_3d_bar(ax, xi, v, width=0.70, color=col, bottom=bot, depth_frac=0.10)
            ax.bar(x, vals*0, bottom=bottoms, color=col, label=p, alpha=0)
            sv.append(vals); sb.append(bottoms.copy()); sc.append(col); bottoms += vals
        _label_stacked(ax, x, sv, sb, sc)
        totals = [q.get("total",0) for q in quarterly]
        mx = max(totals) if totals else 1
        for xi, t in enumerate(totals):
            if t:
                ax.text(xi, t+mx*0.025, str(t), ha="center", va="bottom",
                        fontsize=9, color=_TC_LABEL, fontweight="bold")
        ax.set_xticks(x); ax.set_xticklabels(quarters, fontsize=9, color=_TC_AXIS)
        ax.legend(**_legend_kwargs(), ncol=4)
    ax.set_ylim(bottom=0); fig.tight_layout(pad=0.9)
    return _fig_to_img(fig, 10, 5.0)


def chart_valeo_line(valeo_monthly, title) -> Image:
    months = list(range(len(valeo_monthly)))
    mlabels = [r["month"] for r in valeo_monthly]
    vals    = [r["count"] for r in valeo_monthly]
    fig, ax = plt.subplots(figsize=(11, 3.6))
    _chart_bg(fig, ax, title=title, ylabel="Complaints")
    ax.fill_between(months, vals, alpha=0.22, color=_GLD, zorder=2)
    _glow_line(ax, months, vals, color="#B45309")
    mx = max(vals) if vals else 1
    for i, v in enumerate(vals):
        ax.text(i, v+mx*0.035, str(v), ha="center", va="bottom",
                fontsize=8, color=_TC_LABEL, fontweight="bold")
    ax.set_xticks(months); ax.set_xticklabels(mlabels, fontsize=8.5, color=_TC_AXIS)
    ax.set_ylim(bottom=0); fig.tight_layout(pad=0.9)
    return _fig_to_img(fig, 16, 4.4)


def chart_heatmap_customer_plant(cust_plant_data, plants) -> Optional[Image]:
    if not cust_plant_data:
        return None
    top       = cust_plant_data[:15]
    customers = [r["customer"] for r in top]
    matrix    = np.array([[r.get(p, 0) for p in plants] for r in top], dtype=float)
    fig, ax   = plt.subplots(figsize=(13, max(5.0, len(customers)*0.56+2.2)))
    fig.patch.set_facecolor(_BG_CHART); ax.set_facecolor(_BG_CHART)
    cmap = mcolors.LinearSegmentedColormap.from_list(
        "avo_light", ["#EFF6FF","#BFDBFE","#60A5FA","#2563EB","#1E3A8A"])
    im = ax.imshow(matrix, aspect="auto", cmap=cmap)
    ax.set_xticks(range(len(plants)))
    ax.set_xticklabels(plants, fontsize=7.5, rotation=32, ha="right", color=_TC_AXIS)
    ax.set_yticks(range(len(customers)))
    ax.set_yticklabels(customers, fontsize=7.5, color=_TC_AXIS)
    for sp in ax.spines.values(): sp.set_visible(False)
    ax.tick_params(length=0)
    for i in range(len(customers)):
        for j in range(len(plants)):
            v = int(matrix[i, j])
            if v:
                ax.text(j, i, str(v), ha="center", va="center", fontsize=7,
                        color="white" if matrix[i,j] > matrix.max()*0.5 else "#0A0F1E",
                        fontweight="bold")
    cb = plt.colorbar(im, ax=ax, fraction=0.016, pad=0.02)
    cb.ax.tick_params(labelsize=7.5, colors=_TC_AXIS)
    cb.set_label("Complaints", fontsize=8, color=_TC_AXIS)
    cb.outline.set_edgecolor("#AAAAAA")
    ax.set_title("Customer × Plant Complaint Heatmap", fontsize=11, fontweight="bold",
                 color=_TC_TITLE, pad=9, loc="left")
    fig.tight_layout(pad=0.9)
    return _fig_to_img(fig, 16, max(5.6, len(customers)*0.64+2.6))


def chart_product_line_stacked(pl_plant, plants) -> Optional[Image]:
    if not pl_plant:
        return None
    pl_labels = [r["product_line"] for r in pl_plant]
    fig, ax   = plt.subplots(figsize=(10, max(4.0, len(pl_labels)*0.66+2.0)))
    _chart_bg(fig, ax, title="Complaints by Product Line × Plant")
    ax.spines["left"].set_visible(False)
    y = np.arange(len(pl_labels)); bottoms = np.zeros(len(pl_labels))
    for i, p in enumerate(plants):
        vals = np.array([r.get(p, 0) for r in pl_plant])
        col  = PALETTE[i % len(PALETTE)]
        for yi, (v, b) in enumerate(zip(vals, bottoms)):
            if v > 0:
                _make_3d_bar_h(ax, yi, v, height=0.58, color=col, left=b, depth_frac=0.15)
        ax.barh(y, vals*0, left=bottoms, color=col, label=p, alpha=0)
        for yi, (v, b) in enumerate(zip(vals, bottoms)):
            if v:
                rt = pl_plant[yi].get("total", 1) or 1
                if v/rt >= 0.05:
                    ax.text(b+v/2, yi, str(int(v)), ha="center", va="center",
                            fontsize=6.5, color="white" if _luma(col) < 0.45 else _INK,
                            fontweight="bold")
        bottoms += vals
    totals = [r.get("total",0) for r in pl_plant]
    mx = max(totals) if totals else 1
    for yi, t in enumerate(totals):
        if t:
            ax.text(t+mx*0.015, yi, str(t), va="center",
                    fontsize=7.5, color=_TC_LABEL, fontweight="bold")
    ax.set_yticks(y); ax.set_yticklabels(pl_labels, fontsize=8.5, color=_TC_AXIS)
    ax.legend(**_legend_kwargs(), loc="lower right", ncol=3)
    ax.tick_params(labelsize=8.5, colors=_TC_AXIS); ax.set_ylim(bottom=-0.6)
    fig.tight_layout(pad=0.9)
    return _fig_to_img(fig, 14, max(4.6, len(pl_labels)*0.74+2.2))


def chart_pareto_vertical(rows, label_key, value_key, title,
                           top_n=10, label_transform=None,
                           bar_color=_ELE, line_color=_GLD) -> Optional[Image]:
    cleaned = [r for r in rows if r.get(value_key) not in (None, 0, "")]
    if not cleaned:
        return None
    ranked = sorted(cleaned, key=lambda x: x.get(value_key, 0), reverse=True)[:top_n]
    labels = [textwrap.shorten(str((label_transform or (lambda v: v))(r.get(label_key, "N/A"))),
                                width=18, placeholder="…") for r in ranked]
    vals   = [float(r.get(value_key, 0) or 0) for r in ranked]
    total  = sum(vals)
    if total <= 0:
        return None
    cum_pct = np.cumsum(vals) / total * 100
    x = np.arange(len(labels))

    fig, ax = plt.subplots(figsize=(11.5, 5.0))
    _chart_bg(fig, ax, title=title, ylabel="Claims")
    for i, v in enumerate(vals):
        _make_3d_bar(ax, x[i], v, width=0.62,
                     color=mcolors.to_hex(_gradient_bar_color(bar_color, i, len(vals))))
    mx = max(vals) if vals else 1
    for i, v in enumerate(vals):
        ax.text(x[i], v+mx*0.045,
                f"{int(v) if float(v).is_integer() else v}",
                ha="center", va="bottom", fontsize=8, color=_TC_LABEL,
                fontweight="bold", zorder=7)
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8, color=_TC_AXIS, rotation=35, ha="right")
    ax.set_ylim(0, max(vals)*1.22)

    ax2 = ax.twinx()
    ax2.plot(x, cum_pct, color=line_color, marker="o", linewidth=2.0,
             markersize=4.8, zorder=6)
    ax2.set_ylim(0, 105)
    ax2.set_ylabel("Cumulative %", fontsize=8.5, color=_TC_AXIS)
    ax2.tick_params(axis="y", labelsize=8, colors=_TC_AXIS)
    ax2.spines["top"].set_visible(False); ax2.spines["left"].set_visible(False)
    ax2.spines["right"].set_color("#94A3B8")
    ax2.axhline(80, color="#DC2626", linestyle="--", linewidth=1.0, alpha=0.7)
    ax2.text(len(x)-1, 82, "80%", color="#DC2626", fontsize=8, ha="right")
    ax2.grid(False)
    for i, cp in enumerate(cum_pct):
        ax2.text(x[i]+(0.10 if i==0 else 0), cp+(3.5 if i==0 else 2.5),
                 f"{cp:.0f}%", ha="left" if i==0 else "center",
                 va="bottom", fontsize=7.5, color="#7C4A00", fontweight="bold", zorder=8,
                 bbox=dict(boxstyle="round,pad=0.15", fc="#DCDCDC", ec="none", alpha=0.85))
    ax.bar([0], [0], color=bar_color, label="Claims", alpha=0)
    ax2.plot([], [], color=line_color, marker="o", linewidth=2.4, label="Cumulative %")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1+h2, l1+l2, **_legend_kwargs(), loc="upper left")
    fig.tight_layout(pad=1.0)
    return _fig_to_img(fig, 16, 5.8)


def chart_defect_bar(defect_types, title, top_n=12) -> Optional[Image]:
    top = sorted(defect_types, key=lambda x: x["count"], reverse=True)[:top_n]
    if not top:
        return None
    labels = [textwrap.shorten(r["type"], 28) for r in top]
    vals   = [r["count"] for r in top]
    return chart_hbar(labels[::-1], vals[::-1], title, color=_TEA)


def chart_metric_hbar(rows, label_key, value_key, title, color=_ELE,
                       top_n=10, transform_label=None) -> Optional[Image]:
    cleaned = [r for r in rows if r.get(value_key) not in (None, 0, "")]
    if not cleaned:
        return None
    ranked = sorted(cleaned, key=lambda x: x.get(value_key, 0), reverse=True)[:top_n]
    labels = [textwrap.shorten(
                  str((transform_label or (lambda v: v))(r.get(label_key, "N/A"))),
                  width=28, placeholder="...")
              for r in ranked]
    vals   = [r.get(value_key, 0) for r in ranked]
    return chart_hbar(labels[::-1], vals[::-1], title, color=color)


# ─────────────────────────────────────────────────────────────────────────────
# Table builders
# ─────────────────────────────────────────────────────────────────────────────

def _status_chip(status, color_name, S):
    fg = {"green": "#10B981","orange": "#F59E0B","red": "#EF4444","grey": "#475569"
          }.get(color_name, "#475569")
    label = (status or "n/a").replace("_"," ").title()
    return Paragraph(f'<font color="{fg}"><b>{label}</b></font>', S["td"])


def table_customer_claim_detail(rows, S) -> Optional[Table]:
    if not rows:
        return None
    headers = ["Customer","Claim","Received","Recurring","Type","Product line","Reason","Status"]
    data    = [[Paragraph(h, S["th"]) for h in headers]]
    for r in rows:
        data.append([
            Paragraph(str(r.get("customer","N/A")),    S["td_l"]),
            Paragraph(str(r.get("claim","N/A")),       S["td"]),
            Paragraph(str(r.get("received_on","")),    S["td"]),
            Paragraph(str(r.get("recurring","No")),    S["td"]),
            Paragraph(str(r.get("type","N/A")),        S["td"]),
            Paragraph(str(r.get("product_line","N/A")),S["td"]),
            Paragraph(str(r.get("reason","N/A")),      S["td_l"]),
            _status_chip(r.get("status","n/a"), r.get("status_color","grey"), S),
        ])
    cw  = [_BODY_W * w for w in [0.16,0.13,0.11,0.09,0.10,0.13,0.17,0.11]]
    tbl = Table(data, colWidths=cw, splitByRow=1, repeatRows=1)
    tbl.setStyle(table_style(accent_col=0))
    return tbl


def table_defect_detail(rows, S) -> Optional[Table]:
    if not rows:
        return None
    headers = ["Customer","Claim","Product line","Operation type","Defect type"]
    data    = [[Paragraph(h, S["th"]) for h in headers]]
    for r in rows:
        data.append([
            Paragraph(str(r.get("customer","N/A")),       S["td_l"]),
            Paragraph(str(r.get("claim","N/A")),          S["td"]),
            Paragraph(str(r.get("product_line","N/A")),   S["td"]),
            Paragraph(str(r.get("operation_type","N/A")), S["td_l"]),
            Paragraph(str(r.get("defect_type","N/A")),    S["td_l"]),
        ])
    cw  = [_BODY_W * w for w in [0.18,0.14,0.16,0.26,0.26]]
    tbl = Table(data, colWidths=cw, splitByRow=1, repeatRows=1)
    tbl.setStyle(table_style(accent_col=0))
    return tbl


def table_open_claims_follow_up(rows, S) -> Optional[Table]:
    if not rows:
        return None
    headers = ["Customer","Quality engineer","Claim","Status","Criticality"]
    data    = [[Paragraph(h, S["th"]) for h in headers]]
    for r in rows:
        data.append([
            Paragraph(str(r.get("customer","N/A")),              S["td_l"]),
            Paragraph(str(r.get("quality_engineer","Unassigned")),S["td_l"]),
            Paragraph(str(r.get("claim","N/A")),                 S["td"]),
            _status_chip(r.get("status","n/a"), r.get("status_color","grey"), S),
            Paragraph(str(r.get("criticality",0)),               S["td"]),
        ])
    cw  = [_BODY_W * w for w in [0.26,0.31,0.17,0.12,0.14]]
    tbl = Table(data, colWidths=cw, splitByRow=1, repeatRows=1)
    tbl.setStyle(table_style(accent_col=0))
    return tbl


def table_simple(headers, rows, S, col_widths) -> Optional[Table]:
    if not rows:
        return None
    data = [[Paragraph(h, S["th"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(cell), S["td_l"] if i==0 else S["td"])
                     for i, cell in enumerate(row)])
    tbl = Table(data, colWidths=col_widths, splitByRow=1)
    tbl.setStyle(table_style(accent_col=0))
    return tbl


# ─────────────────────────────────────────────────────────────────────────────
# KPI cards
# ─────────────────────────────────────────────────────────────────────────────
_KPI_ACCENT_COLORS = [C_ELECTRIC, C_GOLD, C_GREEN, C_RED, C_PURPLE, C_TEAL, C_AMBER, C_ROSE]


def kpi_cards(metrics: List[Dict], accent_colors: Optional[List] = None) -> Table:
    S       = build_styles()
    n       = len(metrics)
    col_w   = _BODY_W / n
    accents = (accent_colors or (_KPI_ACCENT_COLORS * 10))[:n]
    cells   = []
    for i, m in enumerate(metrics):
        val_str   = str(m["value"])
        delta     = str(m.get("delta", ""))
        compact   = " ".join(val_str.split())
        main_val  = compact.split("(")[0].strip() if "(" in compact else compact
        sub_val   = ("("+compact.split("(",1)[1]) if "(" in compact else ""
        val_fs    = 22 if len(main_val) <= 7 else 17 if len(main_val) <= 10 else 14
        val_sty   = ParagraphStyle(f"KVA{i}", fontName="Helvetica-Bold", fontSize=val_fs,
                                    textColor=C_NAVY, alignment=TA_CENTER, leading=val_fs+3, spaceAfter=1)
        sub_sty   = ParagraphStyle(f"KSA{i}", fontName="Helvetica", fontSize=6.2,
                                    textColor=C_ELECTRIC, alignment=TA_CENTER, leading=8)
        lbl_sty   = ParagraphStyle(f"KLA{i}", fontName="Helvetica", fontSize=6.5,
                                    textColor=C_SLATE, alignment=TA_CENTER, leading=9)
        if delta:
            dt_style = (S["kpi_delta_bad"]  if delta.startswith("+") else
                        S["kpi_delta_good"] if (delta.startswith("-") and "target" in delta.lower()) else
                        S["kpi_delta_bad"]  if delta.startswith("-") else
                        S["kpi_delta_neu"])
        else:
            dt_style = S["kpi_delta_neu"]
        cell_content = [Paragraph(main_val, val_sty)]
        if sub_val:
            cell_content.append(Paragraph(sub_val, sub_sty))
        cell_content.append(Paragraph(m["label"], lbl_sty))
        if delta:
            cell_content.append(Paragraph(delta, dt_style))
        cells.append(cell_content)

    tbl  = Table([cells], colWidths=[col_w]*n)
    cmds = [
        ("BACKGROUND",    (0,0),(-1,-1), C_WHITE),
        ("BOX",           (0,0),(-1,-1), 0.4, C_MIST),
        ("INNERGRID",     (0,0),(-1,-1), 0.3, C_MIST),
        ("TOPPADDING",    (0,0),(-1,-1), 10),
        ("BOTTOMPADDING", (0,0),(-1,-1), 10),
        ("LEFTPADDING",   (0,0),(-1,-1), 5),
        ("RIGHTPADDING",  (0,0),(-1,-1), 5),
        ("ALIGN",         (0,0),(-1,-1), "CENTER"),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ]
    for i, acc in enumerate(accents):
        cmds.append(("LINEABOVE", (i,0),(i,0), 3.5, acc))
    tbl.setStyle(TableStyle(cmds))
    return tbl


# ─────────────────────────────────────────────────────────────────────────────
# Layout helpers
# ─────────────────────────────────────────────────────────────────────────────

def fit_image(img, max_width, max_height=None):
    if not img or not isinstance(img, Image):
        return img
    draw_w = getattr(img,"drawWidth",None) or getattr(img,"imageWidth",None)
    draw_h = getattr(img,"drawHeight",None) or getattr(img,"imageHeight",None)
    if not draw_w or not draw_h:
        return img
    scale = min(1.0, max_width / float(draw_w))
    if max_height:
        scale = min(scale, max_height / float(draw_h))
    img.drawWidth  = float(draw_w) * scale
    img.drawHeight = float(draw_h) * scale
    return img


def two_col(left_img, right_img) -> Optional[KeepTogether]:
    if left_img is None and right_img is None:
        return None
    if left_img is not None and right_img is not None:
        MAX_H = 76 * mm
        tbl = Table(
            [[fit_image(left_img, _COL_W, MAX_H), fit_image(right_img, _COL_W, MAX_H)]],
            colWidths=[_COL_W+3, _COL_W+3], splitByRow=1,
        )
        tbl.setStyle(TableStyle([
            ("VALIGN", (0,0),(-1,-1), "TOP"),
            ("LEFTPADDING",   (0,0),(-1,-1), 2),
            ("RIGHTPADDING",  (0,0),(-1,-1), 2),
            ("TOPPADDING",    (0,0),(-1,-1), 2),
            ("BOTTOMPADDING", (0,0),(-1,-1), 2),
        ]))
        return KeepTogether([tbl])
    return KeepTogether([fit_image(left_img or right_img, _BODY_W)])


def section_banner(text: str, S: Dict, icon: str = "") -> List:
    clean  = " ".join((text or "").encode("ascii","ignore").decode().split()) or "Section"
    num_s  = ParagraphStyle("SBN2", fontName="Helvetica-Bold", fontSize=10,
                             textColor=C_GOLD, alignment=TA_CENTER, leading=14)
    tit_s  = ParagraphStyle("SBT2", fontName="Helvetica-Bold", fontSize=9.5,
                             textColor=C_WHITE, alignment=TA_LEFT, leading=14)
    parts  = clean.split(".", 1)
    num_p  = Paragraph(parts[0].strip(), num_s) if len(parts)==2 else Paragraph("", num_s)
    tit_p  = Paragraph(parts[1].strip() if len(parts)==2 else clean, tit_s)
    inner  = Table([[Spacer(3*mm,1), num_p, tit_p]],
                   colWidths=[4*mm, 9*mm, _BODY_W-16*mm],
                   style=TableStyle([
                       ("BACKGROUND",(0,0),(0,-1), C_ELECTRIC),
                       ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                       ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0),
                       ("LEFTPADDING",(0,0),(-1,-1),0),
                       ("RIGHTPADDING",(1,0),(1,-1),6),("RIGHTPADDING",(2,0),(2,-1),8),
                   ]))
    banner = Table([[inner]], colWidths=[_BODY_W],
                   style=TableStyle([
                       ("BACKGROUND",(0,0),(-1,-1), C_NAVY),
                       ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
                       ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                       ("LINEBELOW",(0,0),(-1,-1),1.8, C_GOLD),
                   ]))
    return [CondPageBreak(55*mm), Spacer(1,4*mm), banner, Spacer(1,2.5*mm)]


def subsection_label(text: str, S: Dict) -> List:
    lbl = Table([[Paragraph(text, S["section_sm"])]], colWidths=[_BODY_W],
                style=TableStyle([
                    ("LINEBEFORE",(0,0),(-1,-1), 2.5, C_ELECTRIC),
                    ("LEFTPADDING",(0,0),(-1,-1),8),
                    ("TOPPADDING",(0,0),(-1,-1),3),
                    ("BOTTOMPADDING",(0,0),(-1,-1),3),
                ]))
    return [Spacer(1, 2*mm), lbl, Spacer(1, 1.5*mm)]


# ─────────────────────────────────────────────────────────────────────────────
# Document template
# ─────────────────────────────────────────────────────────────────────────────

def build_doc(buf: io.BytesIO, title: str, month: int, year: int) -> BaseDocTemplate:
    m_short   = MONTH_SHORT[month - 1]
    logo_data = get_logo()

    def _hf(canvas, doc):
        canvas.saveState()
        W, H      = PAGE_W, PAGE_H
        HEADER_H  = 22 * mm
        canvas.setFillColor(C_NAVY)
        canvas.rect(0, H-HEADER_H, W, HEADER_H, fill=1, stroke=0)
        canvas.setFillColor(C_COBALT)
        canvas.rect(W*0.62, H-HEADER_H, W*0.38, HEADER_H, fill=1, stroke=0)
        canvas.setFillColor(C_GOLD)
        canvas.rect(0, H-HEADER_H-1.2*mm, W, 1.2*mm, fill=1, stroke=0)
        canvas.setFillColor(C_ELECTRIC)
        canvas.rect(0, H-HEADER_H-2.0*mm, W*0.40, 0.8*mm, fill=1, stroke=0)

        if logo_data:
            try:
                canvas.drawImage(ImageReader(io.BytesIO(logo_data)),
                                 11*mm, H-17.5*mm, width=3.6*cm, height=1.1*cm,
                                 preserveAspectRatio=True, mask="auto")
            except Exception:
                canvas.setFillColor(C_WHITE); canvas.setFont("Helvetica-Bold",13)
                canvas.drawString(11*mm, H-15*mm, "AVOCarbon")
        else:
            canvas.setFillColor(C_WHITE); canvas.setFont("Helvetica-Bold",13)
            canvas.drawString(11*mm, H-15*mm, "AVOCarbon")

        canvas.setFillColor(C_WHITE); canvas.setFont("Helvetica-Bold",10)
        canvas.drawCentredString(W/2, H-12*mm, title)
        canvas.setFont("Helvetica",6.5); canvas.setFillColor(colors.HexColor("#93C5FD"))
        canvas.drawCentredString(W/2, H-18.5*mm,
                                 f"{m_short} {year}  ·  Monthly Quality KPI Report  ·  CONFIDENTIAL")

        canvas.setFont("Helvetica-Bold",9); canvas.setFillColor(C_GOLD)
        canvas.drawRightString(W-10*mm, H-13.5*mm, f"{doc.page:02d}")
        canvas.setFont("Helvetica",6); canvas.setFillColor(C_ICE)
        canvas.drawRightString(W-10*mm, H-18.5*mm, "Page")

        canvas.setStrokeColor(C_MIST); canvas.setLineWidth(0.4)
        canvas.line(13*mm, 13*mm, W-13*mm, 13*mm)
        canvas.setFillColor(C_SLATE); canvas.setFont("Helvetica",5.8)
        canvas.drawString(13*mm, 8*mm, "AVOCarbon Quality Management System  ·  CONFIDENTIAL")
        canvas.drawCentredString(W/2, 8*mm, f"Generated {date.today().strftime('%d %B %Y')}")
        canvas.drawRightString(W-13*mm, 8*mm, "www.avocarbon.com")
        canvas.restoreState()

    frame = Frame(_MARGIN_L, 20*mm, _BODY_W, PAGE_H-48*mm,
                  id="body", leftPadding=0, rightPadding=0, topPadding=4, bottomPadding=4)
    doc   = BaseDocTemplate(buf, pagesize=A4,
                             leftMargin=_MARGIN_L, rightMargin=_MARGIN_R,
                             topMargin=36*mm, bottomMargin=26*mm)
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_hf)])
    return doc