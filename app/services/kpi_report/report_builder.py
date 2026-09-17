"""
app/services/reports/report_builder.py
────────────────────────────────────────
Owner: shared — glues the three layers together.
  - Calls kpi_logic  for data extraction / aggregation
  - Calls report_design for all rendering primitives
  - Calls ai_summary for AI insight panels

No raw SQL, no chart drawing code lives here.
"""

from __future__ import annotations

import io
from typing import Any, Dict

from reportlab.platypus import Spacer

from .kpi_logic import (
    MONTH_SHORT, MONTH_LONG,
    aggregate_criticality_by_customer,
    aggregate_criticality_by_defect,
    aggregate_criticality_by_operation,
    aggregate_criticality_by_plant,
    aggregate_criticality_by_product_line,
    extract_consolidated_kpis,
    extract_pilot_kpis,
    extract_plant_kpis,
    safe_pct,
)
from .report_design import (
    C_AMBER, C_COBALT, C_ELECTRIC, C_GOLD, C_GREEN,
    C_PURPLE, C_RED, C_ROSE, C_TEAL,
    _BODY_W, _ELE, _AMB, _GLD, _PUR, _RED, _ROS, _TEA,
    build_doc, build_styles,
    chart_backlog_rolling, chart_bar_monthly, chart_cs_grouped,
    chart_defect_bar, chart_donut, chart_heatmap_customer_plant,
    chart_hbar, chart_metric_hbar, chart_open_closed_area,
    chart_pareto_vertical, chart_pie, chart_product_line_stacked,
    chart_quarterly_grouped, chart_rolling_claims, chart_rolling_late_steps,
    chart_valeo_line,
    fit_image, kpi_cards, section_banner, subsection_label, table_simple,
    table_customer_claim_detail, table_defect_detail, table_open_claims_follow_up,
    two_col,
    C_INK, C_NAVY, C_WHITE, C_MIST, C_PAPER, C_ICE,
    TableStyle, Table, Paragraph,
)
from .ai_summary import append_ai_summary


# ─────────────────────────────────────────────────────────────────────────────
# PER-PLANT REPORT
# ─────────────────────────────────────────────────────────────────────────────

def per_plant_report(data: Dict[str, Any], plant: str, month: int, year: int) -> bytes:
    S     = build_styles()
    buf   = io.BytesIO()
    doc   = build_doc(buf, f"AVOCarbon  ·  {plant}  ·  Quality KPI", month, year)
    story = []

    kpis     = extract_plant_kpis(data, plant, month, year)
    m_name   = kpis["m_name"]
    m_long   = kpis["m_long"]
    m_count  = kpis["m_count"]
    target   = kpis["target"]
    ytd      = kpis["ytd"]
    st       = kpis["status_totals"]
    cs_rows  = kpis["cs_rows"]

    # Raw data refs
    monthly_vs_target_rolling = data.get("monthly_vs_target_rolling_12_plus_3", [])
    cs_all                    = data.get("cs_type_per_plant_monthly", [])
    oc_all                    = data.get("open_closed_per_plant_monthly", [])
    defect_types              = data.get("defect_types", [])
    valeo_monthly             = data.get("valeo_monthly", [])
    pl_plant_data             = data.get("complaints_by_product_line_plant", [])
    cust_avocarbon            = data.get("complaints_per_customer_avocarbon", [])
    rep_by_plant              = data.get("repetitive_by_plant", [])
    process_pareto            = data.get("process_pareto", [])
    application_pareto        = data.get("application_pareto", [])
    recurrence                = data.get("recurrence_rate", [])
    customer_claim_detail     = data.get("customer_claim_detail_by_plant", [])
    defect_detail_by_plant    = data.get("defect_detail_by_plant", [])
    open_claims_follow_up     = data.get("open_claims_follow_up_by_plant", [])
    quality_engineer_summary  = data.get("quality_engineer_summary", [])
    late_steps_rolling        = data.get("late_steps_rolling_12_plus_3", [])
    backlog_rolling           = data.get("backlog_rolling_15", [])

    oc_rows     = [r for r in oc_all if r.get("plant") == plant]
    rec_row     = kpis["rec_row"]
    cycle_row   = kpis["cycle_row"]

    # ── KPI header ────────────────────────────────────────────────
    story.append(Spacer(1, 3 * 3.5275))  # 3mm
    story.append(kpi_cards([
        {"label": f"{m_long} Complaints", "value": m_count,            "delta": kpis["delta_str"]},
        {"label": f"YTD {year}",          "value": ytd,                "delta": ""},
        {"label": "Monthly Target",       "value": kpis["target_label"], "delta": ""},
        {"label": "Open Complaints",      "value": kpis["total_open"], "delta": ""},
        {"label": "Avg Days to Close",    "value": kpis["avg_days_str"], "delta": "open → closed"},
    ], accent_colors=[C_ELECTRIC, C_COBALT, C_GOLD, C_RED, C_TEAL]))

    story.append(Spacer(1, 2 * 3.5275))
    story.append(kpi_cards([
        {"label": "Recurrence Rate",
         "value": f"{rec_row.get('recurrence_pct',0):.1f}%" if rec_row else "n/a"},
        {"label": "CS1 Quality",  "value": sum(r.get("CS1",0) for r in cs_rows)},
        {"label": "CS2 Warranty", "value": sum(r.get("CS2",0) for r in cs_rows)},
        {"label": "In Progress",  "value": st["In Progress"]},
    ], accent_colors=[C_AMBER, C_ELECTRIC, C_GOLD, C_PURPLE, C_TEAL]))

    story.append(Spacer(1, 4 * 3.5275))

    # ── 1. Complaint trend vs target ──────────────────────────────
    story += section_banner("1. Complaint trend vs target", S)
    trend_img = chart_rolling_claims(
        monthly_vs_target_rolling, plant=plant,
        report_month=month, report_year=year, dyn_target=target,
    )
    append_ai_summary(
        story, S, f"{plant} monthly complaints vs target",
        {"plant": plant, "month": m_name, "monthly_count": m_count,
         "target": target, "ytd": ytd,
         "monthly_vs_target": [r for r in monthly_vs_target_rolling if r.get("plant")==plant]},
        context_note="Assess complaint trend vs target over rolling 12-month window.",
        month=month, year=year, scope=f"plant: {plant}", chart_img=trend_img,
    )

    # ── 2. Customers and product mix ──────────────────────────────
    story += section_banner("2. Customers and product mix", S)
    cust_rows = sorted([r for r in cust_avocarbon if r.get("avocarbon_plant")==plant],
                        key=lambda x: x["count"], reverse=True)[:10]
    c_img  = (chart_hbar([r["customer"] for r in reversed(cust_rows)],
                          [r["count"]   for r in reversed(cust_rows)],
                          "Complaints by customer", color=_ELE) if cust_rows else None)
    pl_rows = [r for r in pl_plant_data if r.get(plant, 0) > 0]
    pl_img  = (chart_pie([r["product_line"] for r in pl_rows],
                          [r.get(plant,0)   for r in pl_rows],
                          f"{plant} — Product line split") if pl_rows else None)
    tc2 = two_col(c_img, pl_img)
    if tc2:
        story.append(tc2)

    plant_claim_rows = [r for r in customer_claim_detail if r.get("plant")==plant]
    if plant_claim_rows:
        story += subsection_label("Customer claim detail", S)
        detail_tbl = table_customer_claim_detail(plant_claim_rows[:20], S)
        if detail_tbl:
            story.append(detail_tbl)

    if valeo_monthly and any(r.get("count",0) for r in valeo_monthly):
        story.append(Spacer(1, 2 * 3.5275))
        story.append(fit_image(
            chart_valeo_line(valeo_monthly, f"Valeo complaints per month — {year}"), _BODY_W,
        ))

    append_ai_summary(
        story, S, f"{plant} customer and product exposure",
        {"plant": plant, "top_customers": cust_rows,
         "product_lines": [{"product_line": r.get("product_line"), "count": r.get(plant,0)}
                           for r in pl_rows]},
        context_note="Identify customer concentration risk and product-line exposure.",
        month=month, year=year, scope=f"plant: {plant}", chart_img=None,
    )

    # ── 3. Defect types and root cause ────────────────────────────
    story += section_banner("3. Defect types and root cause", S)
    def_img = chart_pareto_vertical(
        sorted(defect_types, key=lambda x: x["count"], reverse=True),
        label_key="type", value_key="count",
        title=f"{plant} — Defect type Pareto",
        top_n=10, bar_color=_TEA, line_color=_GLD,
    ) if defect_types else None

    proc_scoped = [{"process": r.get("process"), "count": r.get(plant,0)}
                   for r in process_pareto if r.get(plant,0)]
    proc_img = chart_pareto_vertical(
        sorted(proc_scoped, key=lambda x: x["count"], reverse=True),
        label_key="process", value_key="count",
        title=f"{plant} — Process driver Pareto",
        top_n=10, bar_color=_PUR, line_color=_GLD,
    ) if proc_scoped else None

    tc3 = two_col(def_img, proc_img)
    if tc3:
        story.append(tc3)

    app_scoped = [{"application": r.get("application"), "count": r.get(plant,0)}
                  for r in application_pareto if r.get(plant,0)]
    app_img = chart_pareto_vertical(
        sorted(app_scoped, key=lambda x: x["count"], reverse=True),
        label_key="application", value_key="count",
        title=f"{plant} — Application driver Pareto",
        top_n=10, bar_color=_ELE, line_color=_GLD,
    ) if app_scoped else None

    if app_img:
        story.append(fit_image(app_img, _BODY_W))

    plant_defect_rows = [r for r in defect_detail_by_plant if r.get("plant")==plant]
    if plant_defect_rows:
        story += subsection_label("Defect detail", S)
        defect_tbl = table_defect_detail(plant_defect_rows[:20], S)
        if defect_tbl:
            story.append(defect_tbl)

    rep_rows = [r for r in rep_by_plant if r.get("plant")==plant]
    if rep_rows:
        total_rep  = sum(r["count"] for r in rep_rows)
        rep_sorted = sorted(rep_rows, key=lambda x: x["count"], reverse=True)
        rep_labels = [
            f"{r['repetition_number']}  ({(r['count']/total_rep*100):.0f}%)" if total_rep
            else str(r["repetition_number"]) for r in rep_sorted
        ]
        rep_img = chart_hbar(rep_labels[::-1], [r["count"] for r in rep_sorted][::-1],
                              f"{plant} — Repetition distribution (total: {total_rep})", color=_PUR)
        if rep_img:
            story.append(fit_image(rep_img, _BODY_W))

    append_ai_summary(
        story, S, f"{plant} root-cause and recurrence",
        {"plant": plant,
         "recurrence_pct": rec_row.get("recurrence_pct") if rec_row else None,
         "top_defects": defect_types[:8],
         "process_pareto": [{"process": r.get("process"), "count": r.get(plant,0)}
                            for r in process_pareto if r.get(plant,0)],
         "repetition": rep_rows},
        context_note="Identify whether recurrence suggests ineffective corrective actions or systemic failures.",
        month=month, year=year, scope=f"plant: {plant}", chart_img=None,
    )

    # ── 4. Status and backlog ─────────────────────────────────────
    story += section_banner("4. Status and backlog", S)
    cs_img    = chart_cs_grouped(cs_rows, plant, f"{plant} — CS1 vs CS2 per Month") if cs_rows else None
    donut_img = chart_donut(list(st.keys()), list(st.values()), f"{plant} — Status mix")
    tc5 = two_col(cs_img, donut_img)
    if tc5:
        story.append(tc5)

    oc_img = chart_open_closed_area(oc_rows, f"{plant} — Open vs Closed per Month") if oc_rows else None
    append_ai_summary(
        story, S, f"{plant} status and closure profile",
        {"plant": plant, "status_totals": st,
         "cs_total":  sum(r.get("CS1",0)+r.get("CS2",0) for r in cs_rows),
         "cs2_total": sum(r.get("CS2",0) for r in cs_rows),
         "avg_days_to_close": cycle_row.get("avg_days") if cycle_row else None},
        context_note="Assess backlog health: open vs closed ratio, CS2 warranty pressure, closure speed.",
        month=month, year=year, scope=f"plant: {plant}", chart_img=oc_img,
    )

    late_img = chart_rolling_late_steps(
        late_steps_rolling, plant=plant, report_month=month, report_year=year, dyn_target=0.0,
    )
    if late_img:
        story += subsection_label("Late steps trend", S)
        story.append(fit_image(late_img, _BODY_W))

    backlog_img = chart_backlog_rolling(backlog_rolling, plant=plant,
                                        report_month=month, report_year=year)
    if backlog_img:
        story += subsection_label("Backlog trend", S)
        story.append(fit_image(backlog_img, _BODY_W))

    plant_open_rows = [r for r in open_claims_follow_up if r.get("plant")==plant]
    if plant_open_rows:
        story += subsection_label("Open claims follow-up", S)
        open_tbl = table_open_claims_follow_up(plant_open_rows[:20], S)
        if open_tbl:
            story.append(open_tbl)
        append_ai_summary(
            story, S, f"{plant} quality engineer performance",
            {"plant": plant, "engineers": quality_engineer_summary},
            context_note="Assess workload and performance of quality engineers.",
            month=month, year=year, scope=f"plant: {plant}", chart_img=None,
        )

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# CONSOLIDATED REPORT
# ─────────────────────────────────────────────────────────────────────────────

def consolidated_report(data: Dict[str, Any], month: int, year: int) -> bytes:
    S   = build_styles()
    buf = io.BytesIO()
    doc = build_doc(buf, "AVOCarbon  ·  Quality KPI  ·  All Plants", month, year)
    story = []

    kpis   = extract_consolidated_kpis(data, month, year)
    m_name = kpis["m_name"]
    plants = kpis["plants"]
    st     = kpis["status_totals"]

    # Raw data refs
    monthly_data       = data.get("monthly_data", [])
    quarterly          = data.get("quarterly_by_plant", [])
    defect_types       = data.get("defect_types", [])
    product_types      = data.get("product_types", [])
    valeo_monthly      = data.get("valeo_monthly", [])
    pl_plant_data      = data.get("complaints_by_product_line_plant", [])
    cust_plant_data    = data.get("complaints_by_customer_plant", [])
    process_pareto     = data.get("process_pareto", [])
    application_pareto = data.get("application_pareto", [])
    total_by_plant     = data.get("total_by_plant", [])
    claim_crit_detail  = data.get("claim_criticality_detail", [])
    oc_all             = data.get("open_closed_per_plant_monthly", [])

    criticality_by_plant    = aggregate_criticality_by_plant(claim_crit_detail)
    criticality_by_customer = aggregate_criticality_by_customer(claim_crit_detail)
    criticality_by_defect   = aggregate_criticality_by_defect(claim_crit_detail)
    criticality_by_op       = aggregate_criticality_by_operation(claim_crit_detail)
    criticality_by_pl       = aggregate_criticality_by_product_line(claim_crit_detail)

    # ── KPI header ────────────────────────────────────────────────
    story.append(Spacer(1, 3 * 3.5275))
    story.append(kpi_cards([
        {"label": f"{m_name} Complaints",  "value": kpis["current_month_total"]},
        {"label": f"YTD {year}",           "value": kpis["ytd_total"]},
        {"label": "Open Complaints",       "value": kpis["total_open"]},
        {"label": "Avg Days to Close",
         "value": f"{kpis['avg_days']:.1f}" if kpis["avg_days"] is not None else "n/a"},
        {"label": "Median Days to Close",
         "value": f"{kpis['median_days']:.1f}" if kpis["median_days"] is not None else "n/a"},
        {"label": "Recurrence Rate",       "value": f"{kpis['rec_pct']:.1f}%"},
    ], accent_colors=[C_ELECTRIC, C_COBALT, C_RED, C_TEAL, C_PURPLE, C_AMBER]))

    story.append(Spacer(1, 2 * 3.5275))
    story.append(kpi_cards([
        {"label": "CS2 Total",        "value": kpis["cs2_total"]},
        {"label": "In Progress",      "value": st.get("in_progress",0)},
        {"label": "Active Customers", "value": kpis["active_customers"]},
    ], accent_colors=[C_GOLD, C_TEAL, C_PURPLE, C_ELECTRIC]))

    story.append(Spacer(1, 4 * 3.5275))

    # ── 1. Executive monthly overview ────────────────────────────
    story += section_banner("1. Executive monthly overview", S)
    monthly_img = chart_bar_monthly(monthly_data, title=f"All plants — Monthly complaints {year}")
    q_img = chart_quarterly_grouped(quarterly, title="Complaints by quarter")
    pareto_plant_img = chart_pareto_vertical(
        total_by_plant, label_key="plant", value_key="count",
        title="Pareto per plant — YTD claims", top_n=12, bar_color=_TEA, line_color=_GLD,
    )
    tc = two_col(q_img, pareto_plant_img)
    if tc:
        story.append(tc)
    append_ai_summary(
        story, S, "Group complaint load and plant distribution",
        {"month": m_name, "current_month_total": kpis["current_month_total"],
         "ytd_total": kpis["ytd_total"],
         "monthly_data": monthly_data, "quarterly": quarterly,
         "plant_distribution": total_by_plant},
        context_note="Assess overall complaint load and which plants drive volume vs AVOCarbon -30% target.",
        month=month, year=year, scope="consolidated: all plants", chart_img=monthly_img,
    )

    # ── 2. Customers and exposure ─────────────────────────────────
    story += section_banner("2. Customers and exposure", S)
    hm = chart_heatmap_customer_plant(cust_plant_data, plants)
    if hm:
        story.append(fit_image(hm, _BODY_W))

    top10  = sorted(cust_plant_data, key=lambda x: x.get("total",0), reverse=True)[:10]
    c_img  = (chart_hbar([r["customer"] for r in reversed(top10)],
                          [r["total"]   for r in reversed(top10)],
                          "Top 10 customers by complaint volume") if top10 else None)
    pl_img = chart_product_line_stacked(pl_plant_data, plants) if pl_plant_data else None
    tc2    = two_col(c_img, pl_img)
    if tc2:
        story.append(tc2)

    if top10:
        story += subsection_label("Top customers — group total", S)
        grand = sum(r.get("total",0) for r in top10)
        cust_tbl = table_simple(
            ["Customer","Total","% of top-10"],
            [[r["customer"], r.get("total",0),
              f"{(r.get('total',0)/grand*100):.1f}%" if grand else "n/a"] for r in top10],
            S, [_BODY_W*0.52, _BODY_W*0.24, _BODY_W*0.24],
        )
        if cust_tbl:
            story.append(cust_tbl)

    if valeo_monthly and any(r.get("count",0) for r in valeo_monthly):
        story.append(Spacer(1, 2 * 3.5275))
        story.append(fit_image(chart_valeo_line(valeo_monthly,
                                                 f"Valeo complaints per month — {year}"), _BODY_W))

    append_ai_summary(
        story, S, "Customer and plant exposure",
        {"top_customers": top10, "customer_plant_heatmap": cust_plant_data[:15],
         "product_line_mix": pl_plant_data[:12]},
        context_note="Identify customer concentration across plants. Flag key accounts with high volume.",
        month=month, year=year, scope="consolidated: all plants", chart_img=None,
    )

    # Criticality by plant & customer
    tc_crit = two_col(
        chart_pareto_vertical(criticality_by_plant, label_key="plant", value_key="criticality",
                               title="Pareto per plant — criticality", top_n=12,
                               bar_color=_PUR, line_color=_GLD),
        chart_pareto_vertical(criticality_by_customer, label_key="customer", value_key="criticality",
                               title="Pareto per customer — criticality", top_n=10,
                               bar_color=_ROS, line_color=_GLD),
    )
    if tc_crit:
        story.append(tc_crit)

    # ── 3. Defect types, process and application ──────────────────
    story += section_banner("3. Defect types, process and application analysis", S)
    def_img  = chart_defect_bar(defect_types, "Top defect types — group") if defect_types else None
    proc_img = chart_metric_hbar(process_pareto, "process", "total",
                                  "Top process drivers — group", color=_PUR, top_n=10)
    tc3 = two_col(def_img, proc_img)
    if tc3:
        story.append(tc3)

    app_img  = chart_metric_hbar(application_pareto, "application", "total",
                                  "Top application drivers — group", top_n=10)
    prod_img = (chart_pie([r["type"] for r in product_types[:10]],
                           [r["count"] for r in product_types[:10]], "Product types")
                if product_types else None)
    tc4 = two_col(app_img, prod_img)
    if tc4:
        story.append(tc4)

    append_ai_summary(
        story, S, "Group root-cause structure",
        {"defect_types": defect_types[:12], "process_pareto": process_pareto[:12],
         "application_pareto": application_pareto[:12], "product_types": product_types[:10]},
        context_note="Determine whether complaint load is driven by repeatable process failures or narrow defect portfolio.",
        month=month, year=year, scope="consolidated: all plants", chart_img=None,
    )

    # Technical criticality
    tc_tech = two_col(
        chart_pareto_vertical(criticality_by_defect, label_key="defect_type", value_key="criticality",
                               title="Pareto per defect type — criticality", top_n=10,
                               bar_color=_TEA, line_color=_GLD),
        chart_pareto_vertical(criticality_by_op, label_key="operation_type", value_key="criticality",
                               title="Pareto per operation type — criticality", top_n=10,
                               bar_color=_PUR, line_color=_GLD),
    )
    if tc_tech:
        story.append(tc_tech)
    pl_crit_img = chart_pareto_vertical(
        criticality_by_pl, label_key="product_line", value_key="criticality",
        title="Pareto per product line — criticality", top_n=10, bar_color=_ELE, line_color=_GLD,
    )
    if pl_crit_img:
        story.append(fit_image(pl_crit_img, _BODY_W))
    append_ai_summary(
        story, S, "Technical criticality concentration",
        {"criticality_by_defect": criticality_by_defect,
         "criticality_by_operation": criticality_by_op,
         "criticality_by_product_line": criticality_by_pl},
        context_note="Assess which technical dimensions concentrate the highest weighted complaint severity.",
        month=month, year=year, scope="consolidated: all plants",
    )

    # ── 4. Status, backlog and priority ───────────────────────────
    story += section_banner("4. Status, backlog and priority", S)
    oc_group: Dict[str, Dict[str, int]] = {}
    for r in oc_all:
        m = r["month"]
        oc_group.setdefault(m, {"open":0,"closed":0})
        oc_group[m]["open"]   += r.get("open",0)
        oc_group[m]["closed"] += r.get("closed",0)
    oc_rows_grp = [{"month": m, "open": oc_group[m]["open"], "closed": oc_group[m]["closed"]}
                   for m in MONTH_SHORT if m in oc_group]

    oc_img    = chart_open_closed_area(oc_rows_grp, "Group — Open vs Closed per month") if oc_rows_grp else None
    donut_img = chart_donut(list(st.keys()), list(st.values()), "Overall complaint status")
    tc5 = two_col(oc_img, donut_img)
    if tc5:
        story.append(tc5)

    rec_month_rows = kpis["rec_month_rows"]
    rep_img = chart_metric_hbar(rec_month_rows, "plant", "recurrence_pct",
                                 f"Recurrence rate by plant — {m_name} (%)",
                                 color=_AMB, top_n=12)
    if rep_img:
        story.append(fit_image(rep_img, _BODY_W))

    append_ai_summary(
        story, S, "Backlog and recurrence risk",
        {"status_totals": st, "open_closed_trend": oc_rows_grp,
         "recurrence_current_month": rec_month_rows,
         "avg_days_to_close": kpis["avg_days"],
         "median_days_to_close": kpis["median_days"]},
        context_note="Translate open complaint backlog and recurrence rate into customer-facing risk.",
        month=month, year=year, scope="consolidated: all plants", chart_img=None,
    )

    doc.build(story)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# PROCESS PILOT REPORT
# ─────────────────────────────────────────────────────────────────────────────

def process_pilot_report(data: Dict[str, Any], month: int, year: int) -> bytes:
    S   = build_styles()
    buf = io.BytesIO()
    doc = build_doc(buf, "AVOCarbon  ·  Process Pilot  ·  Quality System", month, year)
    story = []

    kpis   = extract_pilot_kpis(data, month, year)
    m_name = kpis["m_name"]

    # Raw data refs
    monthly_data              = data.get("monthly_data", [])
    total_by_plant            = data.get("total_by_plant", [])
    late_steps_by_plant       = data.get("late_steps_by_plant", [])
    claim_crit_detail         = data.get("claim_criticality_detail", [])
    qe_system                 = data.get("quality_engineer_system", [])

    criticality_by_plant    = aggregate_criticality_by_plant(claim_crit_detail)
    criticality_by_customer = aggregate_criticality_by_customer(claim_crit_detail)

    # ── KPI header ────────────────────────────────────────────────
    story.append(Spacer(1, 3 * 3.5275))
    story.append(kpi_cards([
        {"label": f"YTD {year} Claims", "value": kpis["ytd_total"]},
        {"label": "Open Backlog",       "value": kpis["total_open"]},
        {"label": "Avg Days to Close",
         "value": f"{kpis['avg_days']:.1f}" if kpis["avg_days"] is not None else "n/a"},
        {"label": "Recurrence Rate",    "value": f"{kpis['rec_pct']:.1f}%"},
        {"label": "Active Plants",      "value": len(kpis["plants"])},
    ], accent_colors=[C_ELECTRIC, C_RED, C_TEAL, C_AMBER, C_COBALT]))
    story.append(Spacer(1, 4 * 3.5275))

    # ── 1. Rolling claim volume ───────────────────────────────────
    story += section_banner("1. Number of claims — rolling 12 months + 3 forecast", S)
    monthly_img = chart_bar_monthly(
        monthly_data, title=f"All plants — Monthly claims {year}",
    )
    append_ai_summary(
        story, S, "Group claim volume trend",
        {"month": m_name, "ytd_total": kpis["ytd_total"],
         "plant_distribution": total_by_plant},
        context_note="Assess whether group claim count is trending toward the -30% annual target.",
        month=month, year=year, scope="consolidated: all plants", chart_img=monthly_img,
    )

    # ── 2. Plant criticality Pareto ───────────────────────────────
    story += section_banner("2. Plant performance — criticality Pareto", S)
    tc_perf = two_col(
        chart_pareto_vertical(criticality_by_plant, label_key="plant", value_key="criticality",
                               title="Plant Pareto — weighted criticality score", top_n=12,
                               bar_color=_PUR, line_color=_GLD),
        chart_pareto_vertical(sorted(total_by_plant, key=lambda x: x.get("count",0), reverse=True),
                               label_key="plant", value_key="count",
                               title="Plant Pareto — complaint count (reference)", top_n=12,
                               bar_color=_TEA, line_color=_GLD),
    )
    if tc_perf:
        story.append(tc_perf)
    append_ai_summary(
        story, S, "Plant criticality ranking",
        {"criticality_by_plant": criticality_by_plant, "count_by_plant": total_by_plant},
        context_note="Rank plants by criticality, not just volume.",
        month=month, year=year, scope="consolidated: all plants",
    )

    # ── 3. Late actions ───────────────────────────────────────────
    story += section_banner("3. Late actions — Pareto by plant", S)
    late_img = chart_pareto_vertical(
        sorted(late_steps_by_plant, key=lambda x: x.get("count",0), reverse=True),
        label_key="plant", value_key="count",
        title="Late action steps — Pareto by plant", top_n=12, bar_color=_RED, line_color=_GLD,
    ) if late_steps_by_plant else None
    append_ai_summary(
        story, S, "Late action backlog by plant",
        {"late_steps_by_plant": late_steps_by_plant},
        context_note="Identify which plants accumulate the most overdue 8D steps.",
        month=month, year=year, scope="consolidated: all plants", chart_img=late_img,
    )

    # ── 4. Claims × backlog ───────────────────────────────────────
    story += section_banner("4. Claims × backlog per plant", S)
    combined = kpis["combined"]
    tc_bklg  = two_col(
        chart_pareto_vertical(sorted(combined, key=lambda x: x["ytd_count"], reverse=True),
                               label_key="plant", value_key="ytd_count",
                               title="YTD claims by plant", top_n=12, bar_color=_ELE, line_color=_GLD),
        chart_pareto_vertical(sorted(combined, key=lambda x: x["backlog"], reverse=True),
                               label_key="plant", value_key="backlog",
                               title=f"Open backlog by plant — {m_name} {year}", top_n=12,
                               bar_color=_AMB, line_color=_GLD),
    )
    if tc_bklg:
        story.append(tc_bklg)

    if combined:
        story += subsection_label("YTD claims and open backlog by plant", S)
        crit_map = {r["plant"]: r["criticality"] for r in criticality_by_plant}
        rows_tbl = sorted(combined, key=lambda x: x["backlog"], reverse=True)
        tbl_data = [[Paragraph(h, S["th"])
                     for h in ["Plant","YTD Claims","Open Backlog","Criticality Score"]]]
        for r in rows_tbl:
            tbl_data.append([
                Paragraph(r["plant"], S["td_l"]),
                Paragraph(str(r["ytd_count"]), S["td"]),
                Paragraph(str(r["backlog"]), S["td"]),
                Paragraph(str(crit_map.get(r["plant"],0)), S["td"]),
            ])
        from reportlab.lib.units import mm as _mm
        cw  = [_BODY_W*0.34, _BODY_W*0.22, _BODY_W*0.22, _BODY_W*0.22]
        from .report_design import table_style as _ts
        tbl = Table(tbl_data, colWidths=cw, splitByRow=1)
        tbl.setStyle(_ts())
        story.append(tbl)

    append_ai_summary(
        story, S, "Claim load vs backlog concentration",
        {"combined": combined, "backlog_map": kpis["backlog_map"]},
        context_note="Identify plants where backlog is disproportionate to claim count.",
        month=month, year=year, scope="consolidated: all plants",
    )

    # ── 5. Customer criticality ───────────────────────────────────
    story += section_banner("5. Customer exposure — criticality Pareto", S)
    append_ai_summary(
        story, S, "Customer criticality ranking",
        {"criticality_by_customer": criticality_by_customer[:12]},
        context_note="Identify highest-risk customer accounts by criticality weight.",
        month=month, year=year, scope="consolidated: all plants",
        chart_img=chart_pareto_vertical(
            criticality_by_customer, label_key="customer", value_key="criticality",
            title="Customer Pareto — weighted criticality score", top_n=12,
            bar_color=_ROS, line_color=_GLD,
        ),
    )

    # ── 6. Quality engineer table ─────────────────────────────────
    story += section_banner("6. Quality engineer system status", S)
    if qe_system:
        story += subsection_label("Quality engineer readiness by plant", S)
        has_training = any(r.get("trained_count") is not None for r in qe_system)

        if has_training:
            headers = ["Plant","# QEs","≥1 trained (Y/N)","App available (Y/N)","% trained","Gap"]
            cw = [_BODY_W*w for w in [0.24,0.10,0.17,0.17,0.16,0.16]]
        else:
            headers = ["Plant","# QEs","Open claims","Late claims","Late rate %"]
            cw = [_BODY_W*w for w in [0.30,0.14,0.18,0.18,0.20]]

        tbl_data = [[Paragraph(h, S["th"]) for h in headers]]
        cmds = [
            ("BACKGROUND",    (0,0),(-1,0), C_NAVY),
            ("TEXTCOLOR",     (0,0),(-1,0), C_WHITE),
            ("FONTNAME",      (0,0),(-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0),(-1,-1), 6.8),
            ("ALIGN",         (0,0),(-1,-1), "CENTER"),
            ("TOPPADDING",    (0,0),(-1,-1), 4),
            ("BOTTOMPADDING", (0,0),(-1,-1), 4),
            ("LEFTPADDING",   (0,0),(-1,-1), 5),
            ("RIGHTPADDING",  (0,0),(-1,-1), 5),
            ("LINEBELOW",     (0,0),(-1,0), 2, C_ELECTRIC),
            ("LINEBELOW",     (0,1),(-1,-2), 0.15, C_MIST),
            ("ROWBACKGROUNDS",(0,1),(-1,-1), [C_WHITE, C_PAPER]),
        ]
        total_qe = total_trained = 0

        for row_idx, r in enumerate(qe_system, start=1):
            qe_c = r.get("qe_count", 0); total_qe += qe_c
            if has_training:
                trained  = r.get("trained_count") or 0
                app_ok   = r.get("app_available", False)
                at_least = trained >= 1
                pct_t    = safe_pct(trained, qe_c) or 0
                gap      = max(0, qe_c - trained)
                total_trained += trained
                for col_idx, flag in [(2, at_least), (3, app_ok)]:
                    cmds.append(("BACKGROUND",(col_idx,row_idx),(col_idx,row_idx),
                                  C_GREEN if flag else C_RED))
                    cmds.append(("TEXTCOLOR",(col_idx,row_idx),(col_idx,row_idx), C_WHITE))
                tbl_data.append([
                    Paragraph(r.get("plant",""), S["td_l"]),
                    Paragraph(str(qe_c), S["td"]),
                    Paragraph("Yes" if at_least else "No", S["td"]),
                    Paragraph("Yes" if app_ok   else "No", S["td"]),
                    Paragraph(f"{pct_t:.0f}%", S["td"]),
                    Paragraph(str(gap), S["td"]),
                ])
            else:
                open_c   = r.get("open_claims", 0)
                late_c   = r.get("late_claims", 0)
                late_pct = safe_pct(late_c, open_c) or 0
                if late_pct >= 50:
                    cmds.append(("TEXTCOLOR",(4,row_idx),(4,row_idx), C_RED))
                    cmds.append(("FONTNAME", (4,row_idx),(4,row_idx), "Helvetica-Bold"))
                tbl_data.append([
                    Paragraph(r.get("plant",""), S["td_l"]),
                    Paragraph(str(qe_c),           S["td"]),
                    Paragraph(str(open_c),          S["td"]),
                    Paragraph(str(late_c),          S["td"]),
                    Paragraph(f"{late_pct:.0f}%",   S["td"]),
                ])

        if has_training and total_qe > 0:
            pct_grp = safe_pct(total_trained, total_qe) or 0
            cmds += [("BACKGROUND",(0,-1),(-1,-1), C_ICE),
                     ("FONTNAME",  (0,-1),(-1,-1), "Helvetica-Bold"),
                     ("TEXTCOLOR", (0,-1),(-1,-1), C_NAVY),
                     ("LINEABOVE", (0,-1),(-1,-1), 0.8, C_COBALT)]
            tbl_data.append([
                Paragraph("TOTAL", S["th"]),
                Paragraph(str(total_qe), S["td"]),
                Paragraph("—", S["td"]), Paragraph("—", S["td"]),
                Paragraph(f"{pct_grp:.0f}%", S["td"]),
                Paragraph(str(max(0, total_qe - total_trained)), S["td"]),
            ])

        qe_tbl = Table(tbl_data, colWidths=cw, splitByRow=1)
        qe_tbl.setStyle(TableStyle(cmds))
        story.append(qe_tbl)
        story.append(Spacer(1, 2 * 3.5275))

        append_ai_summary(
            story, S, "Quality engineer system readiness",
            {"qe_system": [{k:v for k,v in r.items() if k!="qe_emails"} for r in qe_system],
             "has_training_data": has_training},
            context_note="Assess system readiness: training gaps, plants without app access, and their correlation with late action rates.",
            month=month, year=year, scope="consolidated: all plants",
        )

    # ── 7. Training coverage chart ────────────────────────────────
    story += section_banner("7. Training coverage by plant (%)", S)
    has_training = any(r.get("trained_count") is not None for r in qe_system)
    if has_training and qe_system:
        import matplotlib.pyplot as plt

        labels_tr = [r.get("plant","") for r in qe_system]
        vals_tr   = [float(safe_pct(r.get("trained_count") or 0, r.get("qe_count") or 1) or 0)
                     for r in qe_system]
        from .report_design import (_chart_bg, _make_3d_bar_h, _label_h,
                                     _legend_kwargs, _fig_to_img, _TC_AXIS,
                                     _GRN, _AMB, _RED, _TC_TARGET)
        n = max(len(labels_tr), 1)
        fig, ax = plt.subplots(figsize=(9, max(3.8, n*0.55+1.4)))
        _chart_bg(fig, ax, title="Training coverage by plant (%)")
        ax.spines["left"].set_visible(False)
        ax.yaxis.grid(False)
        ax.xaxis.grid(True, linestyle="--", alpha=0.4, color="#AAAAAA", linewidth=0.7)
        for i, (lbl, v) in enumerate(zip(labels_tr, vals_tr)):
            col = _GRN if v >= 80 else (_AMB if v >= 50 else _RED)
            _make_3d_bar_h(ax, i, v, height=0.55, color=col, depth_frac=0.18)
        ax.axvline(80, color=_TC_TARGET, linestyle="--", linewidth=1.8, label="80% target", zorder=5)
        ax.set_yticks(list(range(n))); ax.set_yticklabels(labels_tr, fontsize=8.5, color=_TC_AXIS)
        ax.set_xlim(0, 115); ax.set_xlabel("% trained", fontsize=9, color=_TC_AXIS, labelpad=6)
        ax.tick_params(axis="x", labelsize=8.5, colors=_TC_AXIS)
        _label_h(ax, list(range(n)), vals_tr, fmt="{v:.0f}%", fontsize=8)
        ax.legend(**_legend_kwargs())
        fig.tight_layout(pad=0.9)
        tr_img = _fig_to_img(fig, 13, max(4.4, n*0.62+1.8))

        append_ai_summary(
            story, S, "Training coverage by plant",
            {"total_qe": sum(r.get("qe_count",0) for r in qe_system),
             "total_trained": sum(r.get("trained_count") or 0 for r in qe_system),
             "by_plant": [{"plant": r.get("plant"), "pct": v}
                          for r, v in zip(qe_system, vals_tr)]},
            context_note="Assess whether training gaps correlate with higher late-action rates.",
            month=month, year=year, scope="consolidated: all plants", chart_img=tr_img,
        )
    else:
        story.append(Paragraph(
            "Training coverage chart unavailable — trained_count not yet populated.",
            S["note"],
        ))

    doc.build(story)
    return buf.getvalue()


if __name__ == "__main__":
    from app.services.dashboard_service import DashboardService
    from app.db.session import SessionLocal
    import logging
    logging.basicConfig(level=logging.INFO)
 
    PLANT = "MONTERREY"   # change to any valid plant
    MONTH = 3
    YEAR  = 2026
    # Set REPORT to "plant", "consolidated", "pilot", or "all"
    REPORT = "all"
 
    db   = SessionLocal()
    data = DashboardService.get_dashboard_stats(month=MONTH, year=YEAR, db=db)
 
    print("Keys present:", sorted(data.keys()))
    print("late_steps_by_plant:", data.get("late_steps_by_plant"))
    print("quality_engineer_system sample:", data.get("quality_engineer_system", [])[:2])
    print("backlog_rolling_15 rows:", len(data.get("backlog_rolling_15", [])))
    print("late_steps_rolling_12_plus_3 rows:", len(data.get("late_steps_rolling_12_plus_3", [])))
 
    if REPORT in ("plant", "all"):
        pdf = per_plant_report(data, plant=PLANT, month=MONTH, year=YEAR)
        path = f"/tmp/kpi_{PLANT}_{YEAR}_{MONTH:02d}.pdf"
        with open(path, "wb") as f:
            f.write(pdf)
        print(f"Per-plant: {path}")
 
    if REPORT in ("consolidated", "all"):
        pdf = consolidated_report(data, month=MONTH, year=YEAR)
        path = f"/tmp/kpi_consolidated_{YEAR}_{MONTH:02d}.pdf"
        with open(path, "wb") as f:
            f.write(pdf)
        print(f"Consolidated: {path}")
 
    if REPORT in ("pilot", "all"):
        pdf = process_pilot_report(data, month=MONTH, year=YEAR)
        path = f"/tmp/kpi_pilot_{YEAR}_{MONTH:02d}.pdf"
        with open(path, "wb") as f:
            f.write(pdf)
        print(f"Process pilot: {path}")