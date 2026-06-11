"""
api-server/series.py
GET /api/bills/series — full monthly aggregates for the Costs and Usage
tabs. The app caches this and slices it client-side (year/month/category
filters), exactly like the dashboard's fm(). All math goes through
shared.aggregate so every number matches the dashboard, including the
accrual clip month (cutoff_ym).
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from shared.aggregate import (ROWS_SQL, build_aggregates,
                              unit_costs_for_month)

log = logging.getLogger("series")

router = APIRouter()


class Consumption(BaseModel):
    elec_kwh: dict[str, float]
    gas_m3: dict[str, float]
    gas_kwh: dict[str, float]
    water_m3: dict[str, float]


class MonthUnitCosts(BaseModel):
    elec_s_kwh: float | None = None
    gas_s_kwh: float | None = None
    gas_s_m3: float | None = None
    water_eur_m3: float | None = None
    pellets_s_kwh: float | None = None


class Series(BaseModel):
    cutoff_ym: str
    vendors: list[str]
    months: list[str]
    # data[vendor][YYYY-MM] = eur (rounded). Sparse — only months with spend.
    data: dict[str, dict[str, float]]
    consumption: Consumption
    # unit_costs[YYYY-MM] = per-series costs (null where no consumption)
    unit_costs: dict[str, MonthUnitCosts]


def build_series(rows, today=None) -> Series:
    agg = build_aggregates(rows, today=today)
    unit_costs = {
        m: MonthUnitCosts(**unit_costs_for_month(agg, m))
        for m in agg["months"]
    }
    return Series(
        cutoff_ym=agg["cutoff_ym"],
        vendors=agg["vendors"],
        months=agg["months"],
        data=agg["bill_data"],
        consumption=Consumption(
            elec_kwh={m: round(v, 3) for m, v in sorted(agg["elec_kwh"].items())},
            gas_m3={m: round(v, 3) for m, v in sorted(agg["gas_m3"].items())},
            gas_kwh={m: round(v, 3) for m, v in sorted(agg["gas_kwh"].items())},
            water_m3={m: round(v, 3) for m, v in sorted(agg["water_m3"].items())},
        ),
        unit_costs=unit_costs,
    )


@router.get("/api/bills/series", response_model=Series)
async def series_endpoint():
    from main import get_engine
    with get_engine().connect() as conn:
        rows = conn.execute(text(ROWS_SQL)).fetchall()
    return build_series(rows)
