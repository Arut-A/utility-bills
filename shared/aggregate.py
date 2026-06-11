"""
shared/aggregate.py
Single source of truth for bill aggregation: cost-month attribution
(water by period end, transports by service month, insurance split by
period, pellets spread over the Sep-Apr heating season) and the accrual
cutoff (CUTOFF_YM).

Used by BOTH bill-parser (dashboard generation) and api-server
(/api/bills/summary). If the dashboard and the app ever disagree on a
number, the bug is here — not in two diverging copies.

Pure stdlib. `rows` are tuples in ROWS_SQL column order.
"""

from calendar import monthrange
from collections import defaultdict
from datetime import date

ROWS_SQL = (
    "SELECT vendor_category, invoice_date, total_amount,"
    " billing_period_start, billing_period_end,"
    " energy_kwh, gas_m3, water_m3, other_units, unit_type,"
    " raw_pdf_path"
    " FROM parsed_bills"
    " WHERE status = 'success'"
    "   AND invoice_date IS NOT NULL"
    "   AND total_amount IS NOT NULL"
    " ORDER BY vendor_category, invoice_date"
)

VENDOR_DISPLAY = {
    "electricity_transport": "electricity_network",
    "gas_transport":         "gas_network",
}

PELLET_KWH_PER_KG = 4.8


def split_by_month(total, start_str, end_str):
    """Pro-rate an amount across the months its billing period spans."""
    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)
    total_days = (end - start).days + 1
    months = {}
    d = start
    while d <= end:
        y, m = d.year, d.month
        month_end = date(y, m, monthrange(y, m)[1])
        period_end = min(month_end, end)
        days = (period_end - d).days + 1
        key = f"{y}-{m:02d}"
        months[key] = round(total * days / total_days, 2)
        d = date(y, m + 1, 1) if m < 12 else date(y + 1, 1, 1)
    return months


def service_month(start_str):
    """Return YYYY-MM from a billing period date string, or None."""
    if not start_str:
        return None
    d = date.fromisoformat(str(start_str))
    return f"{d.year}-{d.month:02d}"


def cost_month_for(vendor_cat, inv_date, bp_start, bp_end):
    """The month a bill's cost is attributed to (single source of truth, used
    by both the dashboard aggregation and the app's Bills list):
      - water: the billing-period END month (meter spans two months)
      - electricity/gas transport: the service month (billed a month in arrears)
      - everything else: the invoice month
    So a transport bill invoiced in June for May service lands in May, next to
    that month's usage bill — not under June.
    """
    inv_month = str(inv_date)[:7]
    if vendor_cat == "water" and bp_end:
        return service_month(bp_end) or inv_month
    if vendor_cat in ("electricity_transport", "gas_transport") and bp_start:
        return service_month(bp_start) or inv_month
    return inv_month


def build_aggregates(rows, today=None):
    """Aggregate parsed-bill rows into the dashboard's data model.

    Returns a dict with:
      bill_data       {display_vendor: {YYYY-MM: eur}}  (rounded, sorted)
      bill_files      [{v, m, a, f}]  per-bill rows for the bills list
      elec_kwh, gas_m3, gas_kwh, water_m3        {YYYY-MM: units} by service month
      water_eur_svc, elec_net_svc, gas_net_svc   {YYYY-MM: eur}   by service month
      pellet_total_eur, pellet_total_kg, pellet_total_kwh
      cutoff_ym       months >= this are incomplete/forward-allocated
      vendors, months sorted lists
    """
    import os

    bill_data = defaultdict(lambda: defaultdict(float))
    bill_files = []
    elec_kwh = {}
    gas_m3 = {}
    gas_kwh = {}
    water_m3 = {}
    water_eur_svc = {}
    elec_net_svc = {}
    gas_net_svc = {}
    pellet_by_season = defaultdict(lambda: {"eur": 0.0, "kg": 0.0})

    for (vendor_cat, inv_date, total_amt, bp_start, bp_end,
         energy, gas, water, other, utype, raw_path) in rows:
        total_amt = float(total_amt)
        inv_month = str(inv_date)[:7]
        display = VENDOR_DISPLAY.get(vendor_cat, vendor_cat)
        svc = service_month(bp_start)

        # Pellets: collect by heating season (Sep-Apr).
        # Invoice May-Dec -> season starts that year; Jan-Apr -> previous year.
        if vendor_cat == "pellets":
            inv_y, inv_m = int(inv_month[:4]), int(inv_month[5:7])
            season_year = inv_y if inv_m >= 5 else inv_y - 1
            pellet_by_season[season_year]["eur"] += total_amt
            if other:
                pellet_by_season[season_year]["kg"] += float(other)
            continue

        # Insurance: split across the covered period.
        if vendor_cat == "house_insurance" and bp_start and bp_end:
            monthly = split_by_month(total_amt, str(bp_start), str(bp_end))
            for m, amt in monthly.items():
                bill_data["house_insurance"][m] += amt
            continue

        # Network bills by service month (unit-cost alignment).
        if vendor_cat == "electricity_transport" and svc:
            elec_net_svc[svc] = total_amt
        elif vendor_cat == "gas_transport" and svc:
            gas_net_svc[svc] = total_amt

        if vendor_cat == "electricity" and energy:
            elec_kwh[svc or inv_month] = float(energy)

        if vendor_cat == "gas":
            month_key = svc or inv_month
            if gas:
                gas_m3[month_key] = float(gas)
            if energy:
                gas_kwh[month_key] = float(energy)

        # Water meter periods span two months; service month = period END month.
        if vendor_cat == "water":
            water_svc = service_month(bp_end) or svc or inv_month
            if water:
                water_m3[water_svc] = float(water)
            water_eur_svc[water_svc] = total_amt

        # Cost-month attribution for vendors billing in arrears (shared helper).
        cost_month = cost_month_for(vendor_cat, inv_date, bp_start, bp_end)

        bill_data[display][cost_month] += total_amt

        pdf_name = (os.path.basename(str(raw_path))
                    if raw_path and not str(raw_path).startswith("email:") else None)
        bill_files.append({"v": display, "m": cost_month,
                           "a": round(total_amt, 2), "f": pdf_name})

    # Pellets: spread each season's spend evenly over Sep-Apr.
    pellet_total_eur = 0.0
    pellet_total_kg = 0.0
    for season_year, season in pellet_by_season.items():
        pellet_total_eur += season["eur"]
        pellet_total_kg += season["kg"]
        monthly_pellet = round(season["eur"] / 8, 2)
        for offset in range(8):  # Sep..Dec = 0..3, Jan..Apr = 4..7
            y = season_year if offset < 4 else season_year + 1
            mo = 9 + offset if offset < 4 else offset - 3
            bill_data["pellets"][f"{y}-{mo:02d}"] += monthly_pellet

    for v in bill_data:
        bill_data[v] = {m: round(a, 2) for m, a in sorted(bill_data[v].items())}

    cutoff_ym = (today or date.today()).strftime("%Y-%m")
    all_months = sorted(set(m for d in bill_data.values() for m in d))
    all_vendors = sorted(bill_data.keys())

    return {
        "bill_data": dict(bill_data),
        "bill_files": bill_files,
        "elec_kwh": elec_kwh,
        "gas_m3": gas_m3,
        "gas_kwh": gas_kwh,
        "water_m3": water_m3,
        "water_eur_svc": water_eur_svc,
        "elec_net_svc": elec_net_svc,
        "gas_net_svc": gas_net_svc,
        "pellet_total_eur": pellet_total_eur,
        "pellet_total_kg": pellet_total_kg,
        "pellet_total_kwh": pellet_total_kg * PELLET_KWH_PER_KG,
        "cutoff_ym": cutoff_ym,
        "vendors": all_vendors,
        "months": all_months,
    }


def unit_costs_for_month(agg, m):
    """Unit costs for one service month — formulas copied 1:1 from the
    dashboard's buildUnitCostChart() JS. Missing consumption -> None,
    never 0 (a zero-dip in a unit-cost chart is a lie)."""
    data = agg["bill_data"]
    elec_eur = (data.get("electricity", {}).get(m, 0) or 0) + (agg["elec_net_svc"].get(m, 0) or 0)
    gas_eur = (data.get("gas", {}).get(m, 0) or 0) + (agg["gas_net_svc"].get(m, 0) or 0)
    e_kwh = agg["elec_kwh"].get(m)
    g_kwh = agg["gas_kwh"].get(m)
    g_m3 = agg["gas_m3"].get(m)
    w_m3 = agg["water_m3"].get(m)
    water_eur = agg["water_eur_svc"].get(m, 0) or 0
    has_pellet = data.get("pellets", {}).get(m)
    pellet_s_kwh = (agg["pellet_total_eur"] / agg["pellet_total_kwh"] * 100
                    if agg["pellet_total_kwh"] > 0 else None)
    return {
        "elec_s_kwh":  round(elec_eur / e_kwh * 100, 2) if e_kwh else None,
        "gas_s_kwh":   round(gas_eur / g_kwh * 100, 2) if g_kwh else None,
        "gas_s_m3":    round(gas_eur / g_m3 * 100, 2) if g_m3 else None,
        "water_eur_m3": round(water_eur / w_m3, 2) if w_m3 else None,
        "pellets_s_kwh": round(pellet_s_kwh, 2) if (has_pellet and pellet_s_kwh) else None,
    }


def latest_unit_costs(agg):
    """Latest non-null value per unit-cost series, with its month."""
    out = {}
    for m in reversed(agg["months"]):
        uc = unit_costs_for_month(agg, m)
        for k, v in uc.items():
            if v is not None and k not in out:
                out[k] = {"value": v, "month": m}
        if len(out) == 5:
            break
    return out
