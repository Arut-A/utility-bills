"""
api-server/summary.py
GET /api/bills/summary — everything the app's Home screen and the
home-screen widget need in one call. All aggregation goes through
shared.aggregate so the numbers always match the dashboard, including
the accrual clip (CUTOFF_YM).
"""

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from shared.aggregate import (ROWS_SQL, build_aggregates, latest_unit_costs,
                              unit_costs_for_month)

log = logging.getLogger("summary")

router = APIRouter()


class MonthTotal(BaseModel):
    month: str
    total: float


class CategoryAmount(BaseModel):
    category: str
    amount: float
    pct: float


class UnitCost(BaseModel):
    value: float
    month: str
    baseline: Optional[float] = None    # trailing-6mo avg of this series
    pct: Optional[float] = None         # latest vs baseline %


class Deviation(BaseModel):
    category: str
    latest: float
    baseline: float       # trailing-6mo avg for this category
    delta: float          # latest - baseline (signed)
    pct: Optional[float]  # delta / baseline %
    anomaly: bool         # materially out of pattern


class Summary(BaseModel):
    cutoff_ym: str
    month_to_date: float
    month_to_date_bills: int
    latest_month: Optional[str]          # last COMPLETE month
    latest_total: Optional[float]
    prev_month: Optional[str]
    mom_pct: Optional[float]
    monthly_avg_12: Optional[float]
    avg_months_used: int
    grand_total: float
    months_count: int
    top_category: Optional[str]
    top_category_total: Optional[float]
    breakdown_latest: list[CategoryAmount]
    last_12_months: list[MonthTotal]     # complete months only, for sparkline
    unit_costs: dict[str, UnitCost]
    # Decision support: latest complete month vs its trailing-6mo baseline.
    baseline_total: Optional[float]
    latest_vs_baseline_pct: Optional[float]
    deviations: list[Deviation]          # per category, biggest movers first


def _month_total(bill_data: dict, month: str) -> float:
    return round(sum(v.get(month, 0) for v in bill_data.values()), 2)


def build_summary(rows, today=None) -> Summary:
    agg = build_aggregates(rows, today=today)
    bd = agg["bill_data"]
    cutoff = agg["cutoff_ym"]
    complete = [m for m in agg["months"] if m < cutoff]

    latest = complete[-1] if complete else None
    prev = complete[-2] if len(complete) >= 2 else None
    latest_total = _month_total(bd, latest) if latest else None
    prev_total = _month_total(bd, prev) if prev else None
    mom_pct = (round((latest_total - prev_total) / prev_total * 100, 1)
               if latest_total is not None and prev_total else None)

    last12 = complete[-12:]
    totals12 = [_month_total(bd, m) for m in last12]
    avg12 = round(sum(totals12) / len(totals12), 2) if totals12 else None

    grand_total = round(sum(sum(v.values()) for v in bd.values()), 2)

    by_vendor = sorted(((v, round(sum(d.values()), 2)) for v, d in bd.items()),
                       key=lambda x: -x[1])
    top_v, top_total = by_vendor[0] if by_vendor else (None, None)

    breakdown = []
    if latest and latest_total:
        for v, d in sorted(bd.items(), key=lambda kv: -kv[1].get(latest, 0)):
            amt = round(d.get(latest, 0), 2)
            if amt > 0:
                breakdown.append(CategoryAmount(
                    category=v, amount=amt,
                    pct=round(amt / latest_total * 100, 1)))

    mtd = _month_total(bd, cutoff)
    mtd_bills = sum(1 for f in agg["bill_files"] if f["m"] == cutoff)

    # ── Decision support: latest complete month vs trailing-6mo baseline ──
    prior = complete[-7:-1]   # up to 6 complete months before the latest
    baseline_total = None
    latest_vs_baseline_pct = None
    deviations: list[Deviation] = []
    if latest and prior:
        baseline_total = round(sum(_month_total(bd, m) for m in prior) / len(prior), 2)
        if baseline_total > 0 and latest_total is not None:
            latest_vs_baseline_pct = round(
                (latest_total - baseline_total) / baseline_total * 100, 1)
        for v, d in bd.items():
            lv = d.get(latest, 0)
            base = sum(d.get(m, 0) for m in prior) / len(prior)
            delta = lv - base
            if abs(delta) < 1:          # ignore sub-€1 noise
                continue
            pct = round(delta / base * 100, 1) if base > 0 else None
            # Flag spikes (overspend), not drops to zero (usually seasonal,
            # e.g. pellets out of heating season) — those mislead as alerts.
            anomaly = (base > 0 and lv > 0 and pct is not None
                       and pct >= 25 and delta >= 5)
            deviations.append(Deviation(
                category=v, latest=round(lv, 2), baseline=round(base, 2),
                delta=round(delta, 2), pct=pct, anomaly=anomaly))
        deviations.sort(key=lambda x: -abs(x.delta))

    # ── Unit costs with trailing-6mo baseline + trend ──
    uc_by_month = {m: unit_costs_for_month(agg, m) for m in complete}
    latest_uc = latest_unit_costs(agg)
    unit_costs = {}
    for key, info in latest_uc.items():
        hist = [uc_by_month[m][key] for m in complete
                if m < info["month"] and uc_by_month[m][key] is not None][-6:]
        base = round(sum(hist) / len(hist), 2) if hist else None
        pct = round((info["value"] - base) / base * 100, 1) if base else None
        unit_costs[key] = UnitCost(value=info["value"], month=info["month"],
                                   baseline=base, pct=pct)

    return Summary(
        cutoff_ym=cutoff,
        month_to_date=mtd,
        month_to_date_bills=mtd_bills,
        latest_month=latest,
        latest_total=latest_total,
        prev_month=prev,
        mom_pct=mom_pct,
        monthly_avg_12=avg12,
        avg_months_used=len(totals12),
        grand_total=grand_total,
        months_count=len(agg["months"]),
        top_category=top_v,
        top_category_total=top_total,
        breakdown_latest=breakdown,
        last_12_months=[MonthTotal(month=m, total=t)
                        for m, t in zip(last12, totals12)],
        unit_costs=unit_costs,
        baseline_total=baseline_total,
        latest_vs_baseline_pct=latest_vs_baseline_pct,
        deviations=deviations,
    )


@router.get("/api/bills/summary", response_model=Summary)
async def summary_endpoint():
    from main import get_engine
    with get_engine().connect() as conn:
        rows = conn.execute(text(ROWS_SQL)).fetchall()
    return build_summary(rows)
