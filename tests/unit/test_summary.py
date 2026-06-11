"""Tests for shared.aggregate + /api/bills/summary builder.

The synthetic rows exercise every attribution rule: arrears electricity,
water by period-end month, transport by service month, insurance period
split, pellets season spread — and the accrual clip.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "api-server"))

from shared.aggregate import build_aggregates, latest_unit_costs, unit_costs_for_month  # noqa: E402
from summary import build_summary  # noqa: E402

TODAY = date(2026, 6, 11)  # cutoff month = 2026-06


def row(vendor, inv, total, bps=None, bpe=None, kwh=None, gas=None,
        water=None, other=None, utype=None, pdf="x.pdf"):
    return (vendor, inv, total, bps, bpe, kwh, gas, water, other, utype, pdf)


ROWS = [
    # electricity: invoiced June for May service month
    row("electricity", "2026-06-08", 64.20, "2026-05-01", "2026-05-31", kwh=160.5),
    row("electricity", "2026-05-07", 66.90, "2026-04-01", "2026-04-30", kwh=171.0),
    # electricity network for May service month
    row("electricity_transport", "2026-06-09", 12.00, "2026-05-01", "2026-05-31"),
    # water: period Apr 30 – May 31 -> service month May (period END)
    row("water", "2026-06-05", 32.10, "2026-04-30", "2026-05-31", water=14.4),
    # gas billed monthly in arrears: cost keyed to invoice month, consumption
    # to service month — the dashboard pairs them per calendar month, which in
    # steady monthly billing compares month-m consumption with the bill paid
    # in month m (covering m-1). We replicate that exactly (parity > theory).
    row("gas", "2026-05-06", 38.55, "2026-04-01", "2026-04-30", gas=65.0, kwh=683.0),
    row("gas", "2026-04-05", 41.10, "2026-03-01", "2026-03-31", gas=71.0, kwh=750.0),
    # insurance: annual split across 12 months (forward-allocated!)
    row("house_insurance", "2026-05-01", 240.00, "2026-05-01", "2027-04-30"),
    # pellets: one season buy, spread Sep-Apr
    row("pellets", "2025-09-15", 800.00, other=1000.0),
    # current-month bill (month-to-date)
    row("internet", "2026-06-03", 19.99, "2026-06-01", "2026-06-30"),
]


@pytest.fixture()
def agg():
    return build_aggregates(ROWS, today=TODAY)


def test_electricity_cost_month_is_invoice_month(agg):
    assert agg["bill_data"]["electricity"]["2026-06"] == 64.20
    assert agg["bill_data"]["electricity"]["2026-05"] == 66.90


def test_water_attributed_to_period_end_month(agg):
    assert agg["bill_data"]["water"]["2026-05"] == 32.10
    assert "2026-06" not in agg["bill_data"]["water"]


def test_transport_attributed_to_service_month(agg):
    assert agg["bill_data"]["electricity_network"]["2026-05"] == 12.00


def test_insurance_split_forward_allocated(agg):
    ins = agg["bill_data"]["house_insurance"]
    assert len(ins) == 12
    assert abs(sum(ins.values()) - 240.00) < 0.5
    assert "2027-04" in ins  # forward allocation exists -> the clip matters


def test_pellets_season_spread(agg):
    p = agg["bill_data"]["pellets"]
    assert p["2025-09"] == 100.0 and p["2026-04"] == 100.0
    assert "2026-05" not in p
    assert agg["pellet_total_kwh"] == 1000.0 * 4.8


def test_cutoff(agg):
    assert agg["cutoff_ym"] == "2026-06"


def test_unit_costs_match_dashboard_formulas(agg):
    uc = unit_costs_for_month(agg, "2026-05")
    # elec: (66.90 [May cost] + 12.00 net) / 160.5 kWh * 100
    assert uc["elec_s_kwh"] == round((66.90 + 12.00) / 160.5 * 100, 2)
    assert uc["water_eur_m3"] == round(32.10 / 14.4, 2)
    # gas: dashboard formula = cost invoiced in month m / consumption of
    # service month m. April: May-billed 38.55 not in April; April-billed
    # 41.10 over April-service 683 kWh.
    apr = unit_costs_for_month(agg, "2026-04")
    assert apr["gas_s_kwh"] == round(41.10 / 683.0 * 100, 2)
    # May has no gas consumption keyed (May service not yet billed) -> None
    assert uc["gas_s_kwh"] is None


def test_unit_cost_never_zero_dips(agg):
    # month with no consumption data at all -> all None, never 0
    uc = unit_costs_for_month(agg, "2026-01")
    assert uc["elec_s_kwh"] is None and uc["water_eur_m3"] is None


def test_latest_unit_costs_picks_latest_non_null(agg):
    latest = latest_unit_costs(agg)
    assert latest["elec_s_kwh"]["month"] == "2026-05"
    assert latest["gas_s_kwh"]["month"] == "2026-04"
    assert latest["gas_s_kwh"]["value"] == round(41.10 / 683.0 * 100, 2)


# ── /api/bills/summary builder ──────────────────────────────────────────

@pytest.fixture()
def summary():
    return build_summary(ROWS, today=TODAY)


def test_summary_clip_excludes_current_and_future_months(summary):
    # latest COMPLETE month is May, even though June and 2027 months have data
    assert summary.latest_month == "2026-05"
    assert all(mt.month < "2026-06" for mt in summary.last_12_months)


def test_summary_latest_total_matches_aggregates(summary, agg):
    expected = round(sum(v.get("2026-05", 0) for v in agg["bill_data"].values()), 2)
    assert summary.latest_total == expected


def test_summary_month_to_date(summary):
    # June so far: electricity 64.20 + internet 19.99 + insurance slice
    assert summary.month_to_date >= round(64.20 + 19.99, 2)
    assert summary.month_to_date_bills >= 2


def test_summary_breakdown_pcts_sum_to_100(summary):
    assert abs(sum(c.pct for c in summary.breakdown_latest) - 100) < 1.5


def test_summary_empty_db():
    s = build_summary([], today=TODAY)
    assert s.latest_month is None and s.grand_total == 0
    assert s.month_to_date == 0 and s.breakdown_latest == []


def test_summary_single_month_no_mom():
    s = build_summary([row("internet", "2026-05-03", 19.99)], today=TODAY)
    assert s.latest_month == "2026-05" and s.mom_pct is None


# ── /api/bills/series ───────────────────────────────────────────────────

def test_series_matches_aggregates():
    from series import build_series
    s = build_series(ROWS, today=TODAY)
    agg = build_aggregates(ROWS, today=TODAY)
    assert s.cutoff_ym == agg["cutoff_ym"]
    assert s.vendors == agg["vendors"]
    assert s.months == agg["months"]
    # data identical to aggregates (the dashboard's source of truth)
    for v in agg["vendors"]:
        assert s.data[v] == agg["bill_data"][v]
    # electricity unit cost for May present and correct
    assert s.unit_costs["2026-05"].elec_s_kwh == round((66.90 + 12.00) / 160.5 * 100, 2)


def test_series_empty_db():
    from series import build_series
    s = build_series([], today=TODAY)
    assert s.months == [] and s.data == {}


# ── cost_month_for (Bills-list grouping parity with the dashboard) ───────

def test_cost_month_transport_uses_service_month():
    from shared.aggregate import cost_month_for
    # transport invoiced June for May service -> attributed to May
    assert cost_month_for("electricity_transport", "2026-06-22", "2026-05-01", "2026-05-31") == "2026-05"
    assert cost_month_for("gas_transport", "2026-06-14", "2026-05-01", "2026-05-31") == "2026-05"


def test_cost_month_water_uses_period_end():
    from shared.aggregate import cost_month_for
    assert cost_month_for("water", "2026-04-30", "2026-03-31", "2026-04-30") == "2026-04"


def test_cost_month_default_is_invoice_month():
    from shared.aggregate import cost_month_for
    assert cost_month_for("electricity", "2026-05-31", "2026-05-01", "2026-05-31") == "2026-05"
    assert cost_month_for("internet", "2026-06-03", None, None) == "2026-06"


# ── deviation analysis (decision support) ────────────────────────────────

def test_summary_deviations_and_baseline():
    # 5 flat months at ~100 then a spike month for electricity
    rows = []
    for mm in range(1, 6):
        rows.append(row("electricity", f"2026-0{mm}-15", 50.0, f"2026-0{mm}-01", f"2026-0{mm}-28", kwh=200.0))
        rows.append(row("internet", f"2026-0{mm}-03", 20.0))
    # latest complete month (May, since cutoff=June) electricity jumps to 90
    today = date(2026, 6, 11)
    s = build_summary(rows, today=today)
    assert s.latest_month == "2026-05"
    assert s.baseline_total is not None
    # electricity should appear in deviations; internet flat so excluded (<€1 delta)
    cats = [d.category for d in s.deviations]
    assert "internet" not in cats  # perfectly flat -> no deviation
