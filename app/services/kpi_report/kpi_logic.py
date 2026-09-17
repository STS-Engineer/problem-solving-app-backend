"""
app/services/reports/kpi_logic.py
──────────────────────────────────

"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

MONTH_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
MONTH_LONG  = ["January","February","March","April","May","June",
               "July","August","September","October","November","December"]


# ─────────────────────────────────────────────────────────────────────────────
# Target computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_target(data: Dict[str, Any], plant: str) -> float:
    """
    Return the monthly complaint target for a plant.
    Priority: explicit monthly_targets dict → last-year average × 0.70.
    """
    monthly_targets = data.get("monthly_targets") or {}
    if plant in monthly_targets:
        return float(monthly_targets[plant] or 0)
    last_year = data.get("last_year_by_plant") or []
    row = next((r for r in last_year if r.get("plant") == plant), None)
    if row and row.get("count", 0) > 0:
        return round((row["count"] / 12) * 0.70, 2)
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Generic helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_pct(num: float, den: float, digits: int = 1) -> Optional[float]:
    if not den:
        return None
    return round((num / den) * 100, digits)


def find_row(rows: List[Dict], key: str, value: Any) -> Dict:
    return next((r for r in rows if r.get(key) == value), {})


def current_month_entry(monthly_data: List[Dict], month_name: str) -> Dict:
    return next((r for r in monthly_data if r.get("month") == month_name), {})


def add_months(year: int, month: int, delta: int):
    total = year * 12 + (month - 1) + delta
    return total // 12, (total % 12) + 1


def month_label_short(year: int, month: int) -> str:
    return f"{MONTH_SHORT[month - 1]}-{str(year)[-2:]}"


# ─────────────────────────────────────────────────────────────────────────────
# Criticality aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _aggregate_criticality_by_key(rows: List[Dict], key_name: str) -> List[Dict]:
    agg: Dict[str, int] = {}
    for r in rows or []:
        key = r.get(key_name) or "N/A"
        agg[key] = agg.get(key, 0) + int(r.get("criticality", 0) or 0)
    out = [{key_name: k, "criticality": v} for k, v in agg.items() if v > 0]
    out.sort(key=lambda x: x["criticality"], reverse=True)
    return out


def aggregate_criticality_by_plant(rows: List[Dict]) -> List[Dict]:
    return _aggregate_criticality_by_key(rows, "plant")


def aggregate_criticality_by_customer(rows: List[Dict]) -> List[Dict]:
    return _aggregate_criticality_by_key(rows, "customer")


def _aggregate_criticality_by_field(rows: List[Dict], source_key: str,
                                    out_key: Optional[str] = None) -> List[Dict]:
    out_key = out_key or source_key
    agg: Dict[str, int] = {}
    for r in rows or []:
        key = r.get(source_key) or "N/A"
        agg[key] = agg.get(key, 0) + int(r.get("criticality", 0) or 0)
    result = [{out_key: k, "criticality": v} for k, v in agg.items() if v > 0]
    result.sort(key=lambda x: x["criticality"], reverse=True)
    return result


def aggregate_criticality_by_defect(rows: List[Dict]) -> List[Dict]:
    return _aggregate_criticality_by_field(rows, "defect_type")


def aggregate_criticality_by_operation(rows: List[Dict]) -> List[Dict]:
    return _aggregate_criticality_by_field(rows, "operation_type")


def aggregate_criticality_by_product_line(rows: List[Dict]) -> List[Dict]:
    return _aggregate_criticality_by_field(rows, "product_line")


# ─────────────────────────────────────────────────────────────────────────────
# Per-plant KPI extraction  (used by report_builder)
# ─────────────────────────────────────────────────────────────────────────────

def extract_plant_kpis(data: Dict[str, Any], plant: str,
                        month: int, year: int) -> Dict[str, Any]:
    """
    Return a flat dict of all scalar KPIs needed for the per-plant report.
    Keeps report_builder free of raw data-wrangling.
    """
    m_name         = MONTH_SHORT[month - 1]
    monthly_data   = data.get("monthly_data", [])
    recurrence     = data.get("recurrence_rate", [])
    cs_all         = data.get("cs_type_per_plant_monthly", [])
    resolution     = data.get("resolution_cycle_time", {})
    status_monthly = data.get("status_monthly_by_plant", [])

    entry     = current_month_entry(monthly_data, m_name)
    m_count   = entry.get(plant, 0)
    target    = compute_target(data, plant)
    ytd       = sum(r.get(plant, 0) for r in monthly_data)

    cs_rows     = [r for r in cs_all         if r.get("plant") == plant]
    status_rows = [r for r in status_monthly if r.get("plant") == plant]
    cycle_row   = find_row(resolution.get("by_plant", []), "plant", plant)
    rec_row     = next((r for r in recurrence
                        if r.get("plant") == plant and r.get("month") == m_name), {})

    status_totals = {
        "Open":         sum(r.get("open",         0) for r in status_rows),
        "In Progress":  sum(r.get("in_progress",  0) for r in status_rows),
        "Closed":       sum(r.get("closed",       0) for r in status_rows),
        "Cancelled":    sum(r.get("cancelled",    0) for r in status_rows),
    }
    total_open = (status_totals["Open"] + status_totals["In Progress"])

    diff = m_count - target
    delta_str = (f"+{diff:.1f} vs target" if diff > 0 else f"{diff:.1f} vs target") if target else ""

    target_label = (f"{target:.1f} (-30% vs prev yr)"
                    if data.get("last_year_by_plant") else str(int(target)))

    avg_days_str = "n/a"
    if cycle_row:
        d = cycle_row.get("avg_days", 0)
        c = cycle_row.get("count", "")
        avg_days_str = f"{d:.1f}" + (f" ({c})" if c else "")

    return {
        "plant":          plant,
        "month":          month,
        "year":           year,
        "m_name":         m_name,
        "m_long":         MONTH_LONG[month - 1],
        "m_count":        m_count,
        "target":         target,
        "target_label":   target_label,
        "delta_str":      delta_str,
        "ytd":            ytd,
        "total_open":     total_open,
        "status_totals":  status_totals,
        "cs_rows":        cs_rows,
        "cycle_row":      cycle_row,
        "avg_days_str":   avg_days_str,
        "rec_row":        rec_row,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Consolidated KPI extraction  (used by report_builder)
# ─────────────────────────────────────────────────────────────────────────────

def extract_consolidated_kpis(data: Dict[str, Any],
                               month: int, year: int) -> Dict[str, Any]:
    """
    Return all scalar KPIs for the consolidated (all-plants) report.
    """
    m_name       = MONTH_SHORT[month - 1]
    monthly_data = data.get("monthly_data", [])
    recurrence   = data.get("recurrence_rate", [])
    resolution   = data.get("resolution_cycle_time", {})
    status_monthly = data.get("status_monthly", [])
    cs2_sla      = data.get("cs2_sla_compliance", {})
    cust_plant   = data.get("complaints_by_customer_plant", [])

    plants = [k for k in (monthly_data[0] if monthly_data else {})
              if k not in ("month", "total")]

    entry               = current_month_entry(monthly_data, m_name)
    current_month_total = entry.get("total", 0)

    rec_month_rows = [r for r in recurrence if r.get("month") == m_name]
    rec_total      = sum(r.get("total",      0) for r in rec_month_rows)
    rec_repetitive = sum(r.get("repetitive", 0) for r in rec_month_rows)
    rec_pct        = safe_pct(rec_repetitive, rec_total) or 0

    status_totals: Dict[str, int] = {}
    for row in status_monthly:
        for k in ("open","in_progress",
                  "closed","cancelled"):
            status_totals[k] = status_totals.get(k, 0) + row.get(k, 0)

    total_open = (status_totals.get("open", 0)
                  + status_totals.get("in_progress", 0)
                  + status_totals.get("under_review", 0))

    return {
        "month":               month,
        "year":                year,
        "m_name":              m_name,
        "plants":              plants,
        "current_month_total": current_month_total,
        "ytd_total":           data.get("total_complaints", 0),
        "total_open":          total_open,
        "status_totals":       status_totals,
        "rec_month_rows":      rec_month_rows,
        "rec_pct":             rec_pct,
        "avg_days":            resolution.get("avg_days_overall"),
        "median_days":         resolution.get("median_days"),
        "cs2_total":           cs2_sla.get("total_cs2", 0),
        "active_customers":    len(cust_plant[:50]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Process-pilot KPI extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_pilot_kpis(data: Dict[str, Any],
                        month: int, year: int) -> Dict[str, Any]:
    m_name         = MONTH_SHORT[month - 1]
    monthly_data   = data.get("monthly_data", [])
    recurrence     = data.get("recurrence_rate", [])
    resolution     = data.get("resolution_cycle_time", {})
    status_monthly = data.get("status_monthly", [])
    backlog_rolling = data.get("backlog_rolling_15", [])
    total_by_plant  = data.get("total_by_plant", [])

    plants = [k for k in (monthly_data[0] if monthly_data else {})
              if k not in ("month", "total")]

    status_totals: Dict[str, int] = {}
    for row in status_monthly:
        for k in ("open","in_progress",
                  "closed","cancelled"):
            status_totals[k] = status_totals.get(k, 0) + row.get(k, 0)

    total_open = (status_totals.get("open", 0)
                  + status_totals.get("in_progress", 0))

    rec_month_rows = [r for r in recurrence if r.get("month") == m_name]
    rec_total      = sum(r.get("total", 0) for r in rec_month_rows)
    rec_repetitive = sum(r.get("repetitive", 0) for r in rec_month_rows)
    rec_pct        = safe_pct(rec_repetitive, rec_total) or 0

    # Backlog snapshot for report month
    backlog_map: Dict[str, int] = {}
    for r in backlog_rolling:
        if (int(r.get("year", 0)) == year
                and int(r.get("month_num", 0)) == month
                and not r.get("is_future")):
            backlog_map[r["plant"]] = int(r.get("actual", 0) or 0)

    combined = [
        {"plant": r.get("plant", ""),
         "ytd_count": r.get("count", 0),
         "backlog": backlog_map.get(r.get("plant", ""), 0)}
        for r in total_by_plant
    ]

    return {
        "month":          month,
        "year":           year,
        "m_name":         m_name,
        "plants":         plants,
        "total_open":     total_open,
        "rec_pct":        rec_pct,
        "avg_days":       resolution.get("avg_days_overall"),
        "ytd_total":      data.get("total_complaints", 0),
        "backlog_map":    backlog_map,
        "combined":       combined,        # plant × ytd_count × backlog
    }