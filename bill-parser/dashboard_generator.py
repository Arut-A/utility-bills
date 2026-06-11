"""
bill-parser/dashboard_generator.py
Regenerate dashboard.html fully from DB — no hardcoded data.
"""
import json
import logging
import os

from db import get_engine
from shared.aggregate import (build_aggregates, ROWS_SQL,
                              VENDOR_DISPLAY as _VENDOR_DISPLAY,
                              PELLET_KWH_PER_KG)
from sqlalchemy import text

log = logging.getLogger("dashboard_generator")

DASHBOARD_PATH = os.environ.get("DASHBOARD_PATH", "/data/dashboard.html")
DASHBOARD_BASE_URL = os.environ.get("DASHBOARD_BASE_URL", "")



def _load_vendor_config():
    """Load colors and labels from vendors.yaml if available."""
    colors = {
        'electricity':'#f59e0b','electricity_network':'#fcd34d',
        'gas':'#3b82f6','gas_network':'#93c5fd',
        'water':'#06b6d4',
        'house_insurance':'#14b8a6','internet':'#10b981','home_security':'#10b981',
        'phone':'#8b5cf6','garbage':'#6b7280','pellets':'#ef4444',
    }
    labels = {
        'electricity':'Electricity','electricity_network':'Electricity Network',
        'gas':'Gas','gas_network':'Gas Network',
        'water':'Water',
        'house_insurance':'House Insurance','internet':'Internet','home_security':'Home Security',
        'phone':'Phone','garbage':'Garbage','pellets':'Pellets',
    }
    try:
        import yaml
        cfg_path = os.environ.get("VENDOR_CONFIG_PATH", "/data/config/vendors.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f)
            for slug, vc in (cfg.get("vendors") or {}).items():
                display = _VENDOR_DISPLAY.get(slug, slug)
                dash = vc.get("dashboard") or {}
                if dash.get("color"):
                    colors[display] = dash["color"]
                if dash.get("label"):
                    labels[display] = dash["label"]
    except Exception as e:
        log.debug("Could not load vendor config for colors/labels: %s", e)
    return colors, labels


def _query_all() -> dict:
    """Query DB and return all data needed for the dashboard."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(ROWS_SQL)).fetchall()
    return rows


def generate_dashboard() -> str | None:
    try:
        rows = _query_all()
        if not rows:
            log.warning("No bill data in DB")
            return None

        agg = build_aggregates(rows)
        bill_data       = agg["bill_data"]
        bill_files      = agg["bill_files"]
        elec_kwh        = agg["elec_kwh"]
        gas_m3          = agg["gas_m3"]
        gas_kwh         = agg["gas_kwh"]
        water_m3        = agg["water_m3"]
        water_eur_svc   = agg["water_eur_svc"]
        elec_net_svc    = agg["elec_net_svc"]
        gas_net_svc     = agg["gas_net_svc"]
        pellet_total_eur = agg["pellet_total_eur"]
        pellet_total_kg  = agg["pellet_total_kg"]
        pellet_total_kwh = agg["pellet_total_kwh"]
        cutoff_ym       = agg["cutoff_ym"]
        all_months      = agg["months"]
        all_vendors     = agg["vendors"]
        raw_json = {"vendors": all_vendors, "months": all_months, "data": bill_data}

        # Build HTML — inline Chart.js so the file works offline (Telegram's
        # in-app viewer blocks CDN scripts; opening dashboard.html locally
        # without internet would also fail otherwise).
        chartjs_path = os.path.join(os.path.dirname(__file__), "chart.umd.min.js")
        try:
            with open(chartjs_path, "r", encoding="utf-8") as f:
                chartjs_inline = f.read()
            # Defensive: escape </script> in case the minifier ever emits it
            # in a string literal — would otherwise close the host <script> tag.
            chartjs_inline = chartjs_inline.replace("</script>", "<\\/script>")
        except FileNotFoundError:
            log.warning("chart.umd.min.js not found at %s; falling back to CDN", chartjs_path)
            chartjs_inline = '/* fallback */ document.write(\'<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"><\\/script>\');'
        html = _TEMPLATE_TOP.replace("__CHARTJS_INLINE__", chartjs_inline)
        html += "const RAW = " + json.dumps(raw_json, ensure_ascii=False) + ";\n"
        html += "const CONSUMPTION = " + json.dumps({
            "elec_kwh":  {m: round(v, 3) for m, v in sorted(elec_kwh.items())},
            "gas_m3":    {m: round(v, 3) for m, v in sorted(gas_m3.items())},
            "gas_kwh":   {m: round(v, 3) for m, v in sorted(gas_kwh.items())},
            "water_m3":  {m: round(v, 3) for m, v in sorted(water_m3.items())},
        }) + ";\n"
        html += "const ELEC_NET_BY_SVC = " + json.dumps(
            {m: round(v, 2) for m, v in sorted(elec_net_svc.items())}) + ";\n"
        html += "const GAS_NET_BY_SVC = " + json.dumps(
            {m: round(v, 2) for m, v in sorted(gas_net_svc.items())}) + ";\n"
        html += "const WATER_EUR_BY_SVC = " + json.dumps(
            {m: round(v, 2) for m, v in sorted(water_eur_svc.items())}) + ";\n"
        html += f"const PELLET_TOTAL_EUR = {round(pellet_total_eur, 2)};\n"
        html += f"const PELLET_TOTAL_KG = {round(pellet_total_kg, 1)};\n"
        html += f"const PELLET_KWH_PER_KG = {PELLET_KWH_PER_KG};\n"
        html += "const PELLET_TOTAL_KWH = PELLET_TOTAL_KG * PELLET_KWH_PER_KG;\n"
        html += "const PELLET_S_KWH = PELLET_TOTAL_KWH > 0 ? PELLET_TOTAL_EUR / PELLET_TOTAL_KWH * 100 : 0;\n"
        html += "const BILL_FILES = " + json.dumps(bill_files, ensure_ascii=False) + ";\n"
        html += f"const BASE_URL = '{DASHBOARD_BASE_URL}';\n"
        html += f"const CUTOFF_YM = '{cutoff_ym}';  // months >= this are forward-allocated (prepaid), excluded from Latest/rolling avg/forecast\n"

        # Load colors/labels from vendor config
        colors, labels = _load_vendor_config()
        html += "const COLORS = " + json.dumps(colors, ensure_ascii=False) + ";\n"
        html += "const LABELS = " + json.dumps(labels, ensure_ascii=False) + ";\n"

        # Inject ICONS map
        icons = {
            'electricity':'\u26a1','electricity_network':'\U0001f50c',
            'gas':'\U0001f525','gas_network':'\U0001f3ed',
            'water':'\U0001f4a7','phone':'\U0001f4f1','internet':'\U0001f310',
            'home_security':'\U0001f512','garbage':'\U0001f5d1\ufe0f',
            'pellets':'\U0001fab5','house_insurance':'\U0001f3e0',
        }
        html += "const ICONS = " + json.dumps(icons, ensure_ascii=False) + ";\n"
        html += _TEMPLATE_BOTTOM

        with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
            f.write(html)

        log.info("Dashboard regenerated: %d vendors, %d months", len(all_vendors), len(all_months))
        return DASHBOARD_PATH

    except Exception as exc:
        log.exception("Dashboard generation failed: %s", exc)
        return None


_TEMPLATE_TOP = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Home Utility Bills Dashboard</title>
<script>__CHARTJS_INLINE__</script>
<style>
  :root { --bg:#0f1117; --surface:#1a1d27; --border:#2a2d3a; --text:#e2e8f0; --muted:#64748b; --up:#ef4444; --down:#22c55e; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:'Segoe UI',system-ui,sans-serif; padding:24px; }
  h1 { font-size:1.6rem; font-weight:700; margin-bottom:4px; }
  .subtitle { color:var(--muted); font-size:0.85rem; margin-bottom:24px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:12px; margin-bottom:28px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }
  .card-label { font-size:0.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:6px; }
  .card-value { font-size:1.4rem; font-weight:700; }
  .card-sub { font-size:0.75rem; color:var(--muted); margin-top:2px; }
  .filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:20px; }
  .chip { padding:5px 14px; border-radius:20px; font-size:0.78rem; cursor:pointer; border:1.5px solid transparent; transition:all .15s; user-select:none; display:flex; align-items:center; gap:6px; }
  .chip.active { color:#fff; }
  .chip:not(.active) { background:var(--surface); border-color:var(--border); color:var(--muted); }
  .chart-wrap { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:20px; margin-bottom:28px; }
  .chart-scroll { overflow-x:auto; }
  .chart-inner { height:350px; }
  .chart-title { font-size:0.9rem; font-weight:600; margin-bottom:16px; color:var(--muted); }
  .grid-2 { display:grid; grid-template-columns:1fr 1fr; gap:24px; margin-bottom:28px; }
  .grid-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:24px; margin-bottom:28px; }
  @media (max-width:900px) { .grid-2, .grid-3 { grid-template-columns:1fr; } }
  @media (max-width:600px) {
    body { padding:12px; }
    h1 { font-size:1.3rem; }
    .sticky-filters { margin:0 -12px; padding-left:12px; padding-right:12px; }
    .filters { flex-wrap:nowrap; overflow-x:auto; scrollbar-width:none; -ms-overflow-style:none; gap:6px; padding-bottom:4px; }
    .filters::-webkit-scrollbar { display:none; }
    .chip { padding:7px 12px; font-size:0.75rem; flex-shrink:0; }
    .cards { grid-template-columns:1fr 1fr; gap:8px; margin-bottom:20px; }
    .card { padding:10px 12px; }
    .card-value { font-size:1.15rem; }
    .chart-wrap { padding:12px; margin-bottom:16px; border-radius:10px; }
    .chart-inner { height:260px; }
    .section-title { font-size:0.95rem; margin:20px 0 12px 0; }
    .grid-2 { gap:16px; }
    .bill-item { padding:10px 12px; gap:10px; }
    .meter-card { padding:12px; }
    .meter-value-big { font-size:1.15rem; }
  }
  .table-wrap { background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; margin-bottom:28px; max-height:600px; overflow-y:auto; }
  table { width:100%; border-collapse:collapse; font-size:0.83rem; }
  thead tr { background:#0f1117; position:sticky; top:0; z-index:1; }
  th { padding:10px 14px; text-align:left; color:var(--muted); font-weight:500; font-size:0.75rem; text-transform:uppercase; letter-spacing:.05em; }
  td { padding:9px 14px; border-top:1px solid var(--border); }
  tr:hover td { background:rgba(255,255,255,.03); }
  .amount { text-align:right; font-variant-numeric:tabular-nums; font-weight:600; }
  .dot { display:inline-block; width:8px; height:8px; border-radius:50%; }
  .change-up { color:var(--up); }
  .change-down { color:var(--down); }
  .section-title { font-size:1.1rem; font-weight:600; margin:32px 0 16px 0; color:var(--text); border-bottom:1px solid var(--border); padding-bottom:8px; }
  .sticky-filters { position:sticky; top:0; z-index:10; background:var(--bg); padding:12px 0 4px 0; margin:0 -24px; padding-left:24px; padding-right:24px; border-bottom:1px solid var(--border); margin-bottom:20px; }
  .filter-label { font-size:0.68rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:4px; }
  .card-change { font-size:0.75rem; font-weight:600; margin-top:4px; }
  .card-change.up { color:var(--up); }
  .card-change.down { color:var(--down); }
  .bill-item { display:flex; align-items:center; gap:12px; padding:12px 14px; background:var(--surface); border:1px solid var(--border); border-radius:10px; margin-bottom:6px; cursor:pointer; transition:background .15s; }
  .bill-item:hover { background:#222531; }
  .bill-icon { width:36px; height:36px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-size:18px; flex-shrink:0; }
  .bill-info { flex:1; min-width:0; }
  .bill-info .name { font-size:0.82rem; font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .bill-info .date { font-size:0.72rem; color:var(--muted); margin-top:2px; }
  .bill-amount-col { text-align:right; flex-shrink:0; }
  .bill-amount-col .eur { font-size:0.9rem; font-weight:700; color:var(--text); }
  .bill-amount-col .pdf-link { font-size:0.7rem; color:#6366f1; margin-top:2px; text-decoration:none; display:block; }
  .bill-amount-col .pdf-link:hover { text-decoration:underline; }
  .bill-month-header { font-size:0.75rem; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; margin:16px 0 8px 0; }
  .bill-month-header:first-child { margin-top:4px; }
  .bills-list-wrap { max-height:600px; overflow-y:auto; }
  .bills-list-wrap::-webkit-scrollbar { width:4px; }
  .bills-list-wrap::-webkit-scrollbar-thumb { background:var(--border); border-radius:4px; }
  .meter-card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:16px; margin-bottom:10px; }
  .meter-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
  .meter-title { font-size:0.9rem; font-weight:600; color:var(--text); }
  .meter-value-big { font-size:1.4rem; font-weight:700; color:var(--text); }
  .meter-unit { font-size:0.8rem; color:var(--muted); }
  .meter-date { font-size:0.72rem; color:var(--muted); margin-top:2px; }
  .meter-delta { font-size:0.75rem; font-weight:600; margin-top:2px; }
  .meter-delta.up { color:var(--up); }
  .meter-delta.down { color:var(--down); }
</style>
</head>
<body>
<h1>Home Utility Bills</h1>
<p class="subtitle">Parsed from Gmail — all amounts include VAT — auto-generated</p>
<div class="sticky-filters">
  <div class="filter-label">Years</div>
  <div class="filters" id="yearFilters" style="margin-bottom:4px"></div>
  <div class="filter-label">Months</div>
  <div class="filters" id="monthFilters" style="margin-bottom:4px"></div>
  <div class="filter-label">Categories</div>
  <div class="filters" id="filters" style="margin-bottom:4px"></div>
</div>
<div class="cards" id="summaryCards"></div>
<div class="chart-wrap">
  <div class="chart-title">Monthly Cost by Category (EUR incl. VAT)</div>
  <div class="chart-scroll" id="costScroll"><div class="chart-inner" id="costInner"><canvas id="chart"></canvas></div></div>
</div>
<div class="chart-wrap">
  <div class="chart-title">Unit Cost (per kWh / m³, incl. network &amp; VAT)</div>
  <div class="grid-3" style="margin-bottom:0">
    <div><div style="height:200px"><canvas id="unitElecChart"></canvas></div></div>
    <div><div style="height:200px"><canvas id="unitWaterChart"></canvas></div></div>
    <div><div style="height:200px"><canvas id="unitPelletsChart"></canvas></div></div>
  </div>
  <div class="grid-2" style="margin-top:16px;margin-bottom:0">
    <div><div style="height:200px"><canvas id="unitGasKwhChart"></canvas></div></div>
    <div><div style="height:200px"><canvas id="unitGasM3Chart"></canvas></div></div>
  </div>
</div>

<div class="grid-2">
  <div class="chart-wrap">
    <div class="chart-title">Spending by Category</div>
    <div style="height:300px"><canvas id="pieChart"></canvas></div>
  </div>
  <div class="chart-wrap">
    <div class="chart-title">Year-over-Year Comparison</div>
    <div class="table-wrap" style="background:transparent;border:none;max-height:300px;">
      <table><thead><tr><th>Year</th><th class="amount">Total (EUR)</th><th class="amount">Change</th><th class="amount">Monthly Avg</th></tr></thead>
      <tbody id="yoyBody"></tbody></table>
    </div>
  </div>
</div>

<div class="chart-wrap">
  <div class="chart-title">Month-over-Month Comparison (same month across years, stacked by category)</div>
  <div class="chart-scroll"><div class="chart-inner" id="momInner"><canvas id="momChart"></canvas></div></div>
</div>

<div class="section-title">Category Breakdown</div>
<div class="cards" id="categoryCards"></div>

<div class="chart-wrap">
  <div class="chart-title">Consumption Trends (Usage, not Cost)</div>
  <div class="grid-2" style="margin-bottom:0">
    <div><div style="height:200px"><canvas id="consElecChart"></canvas></div></div>
    <div><div style="height:200px"><canvas id="consWaterChart"></canvas></div></div>
  </div>
  <div style="height:200px;margin-top:16px"><canvas id="consGasChart"></canvas></div>
</div>

<div class="chart-wrap">
  <div class="chart-title">Budget Forecast (3-Month Projection)</div>
  <div class="chart-scroll"><div style="height:350px"><canvas id="forecastChart"></canvas></div></div>
</div>

<div class="section-title">Meter Readings</div>
<div id="meterCards"></div>
<div class="chart-wrap">
  <div class="chart-title">Meter History</div>
  <div class="grid-2" style="margin-bottom:0">
    <div><div style="height:200px"><canvas id="meterElecChart"></canvas></div></div>
    <div><div style="height:200px"><canvas id="meterWaterChart"></canvas></div></div>
  </div>
  <div style="height:200px;margin-top:16px"><canvas id="meterGasChart"></canvas></div>
</div>

<div class="section-title">Bills by Month</div>
<div class="chart-wrap">
  <div class="bills-list-wrap" id="billsList"></div>
</div>

<script>
"""

_TEMPLATE_BOTTOM = r"""
function iLabel(v){return (ICONS[v]||'')+' '+(LABELS[v]||v);}
const {vendors,months,data}=RAW;
let active=new Set(vendors);
const allYears=[...new Set(months.map(m=>m.slice(0,4)))].sort();
let activeYears=new Set(allYears);
const MONTH_NAMES=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const allMonthNums=[...new Set(months.map(m=>m.slice(5,7)))].sort();
let activeMonths=new Set(allMonthNums);
function fm(){return months.filter(m=>activeYears.has(m.slice(0,4))&&activeMonths.has(m.slice(5,7)));}
function fmtMonth(m){const[y,mo]=m.split('-');return['','Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+mo]+" '"+y.slice(2);}

function updateCards(){
  const _fm=fm();
  // Latest Month + rolling avg use only fully-completed months (exclude
  // forward-allocated prepaid entries so they don't show partial-month spend).
  const _fmComplete=_fm.filter(m=>m<CUTOFF_YM);
  const latestMonth=_fmComplete[_fmComplete.length-1]||_fm[_fm.length-1]||months[months.length-1];
  const last12=_fmComplete.slice(-12);
  let grandTotal=0,latestTotal=0,sum12=0;
  vendors.filter(v=>active.has(v)).forEach(v=>{_fm.forEach(m=>grandTotal+=(data[v]||{})[m]||0);latestTotal+=(data[v]||{})[latestMonth]||0;last12.forEach(m=>sum12+=(data[v]||{})[m]||0);});
  let prevTotal=0,changeHtml='';
  const prevMonth=_fmComplete.length>=2?_fmComplete[_fmComplete.length-2]:null;
  if(prevMonth){vendors.filter(v=>active.has(v)).forEach(v=>prevTotal+=(data[v]||{})[prevMonth]||0);
    if(prevTotal>0){const pct=((latestTotal-prevTotal)/prevTotal*100);const isUp=pct>0;changeHtml=`<div class="card-change ${isUp?'up':'down'}">${isUp?'\u25b2':'\u25bc'} ${Math.abs(pct).toFixed(1)}% vs ${fmtMonth(prevMonth)}</div>`;}}
  const topV=vendors.filter(v=>active.has(v)).map(v=>[v,_fm.reduce((s,m)=>s+((data[v]||{})[m]||0),0)]).sort((a,b)=>b[1]-a[1])[0]||['\u2014',0];
  const n12=Math.min(12,last12.length||1);
  const cards=[{l:'Latest Month',v:'\u20ac'+latestTotal.toFixed(2),s:fmtMonth(latestMonth),extra:changeHtml},{l:'Monthly Avg',v:'\u20ac'+(sum12/n12).toFixed(2),s:'last '+n12+' months'},{l:'Total',v:'\u20ac'+grandTotal.toFixed(0),s:_fm.length+' months'},{l:'Top Category',v:iLabel(topV[0]),s:'\u20ac'+topV[1].toFixed(0)+' total'}];
  const el=document.getElementById('summaryCards');
  el.innerHTML='';
  cards.forEach(c=>{el.insertAdjacentHTML('beforeend',`<div class="card"><div class="card-label">${c.l}</div><div class="card-value">${c.v}</div><div class="card-sub">${c.s}</div>${c.extra||''}</div>`);});
}
function _makeToggleChip(label,isAll,color,onAll,onNone){
  const chip=document.createElement('div');chip.className='chip'+(isAll?' active':'');
  chip.style.cssText=isAll?`background:${color};border-color:${color};color:#fff`:`background:var(--surface);border-color:${color};color:var(--muted)`;
  chip.textContent=label;chip.onclick=isAll?onNone:onAll;return chip;
}
function buildYearFilters(){
  const c=document.getElementById('yearFilters');c.innerHTML='';
  const allActive=allYears.every(y=>activeYears.has(y));
  c.appendChild(_makeToggleChip(allActive?'Clear':'All',allActive,'#6366f1',
    ()=>{allYears.forEach(y=>activeYears.add(y));buildYearFilters();rebuildAll();},
    ()=>{activeYears.clear();activeYears.add(allYears[allYears.length-1]);buildYearFilters();rebuildAll();}));
  allYears.forEach(y=>{const chip=document.createElement('div');chip.className='chip'+(activeYears.has(y)?' active':'');chip.dataset.year=y;chip.style.cssText=activeYears.has(y)?'background:#6366f1;border-color:#6366f1;color:#fff':'background:var(--surface);border-color:#6366f1;color:var(--muted)';chip.textContent=y;chip.onclick=()=>{if(activeYears.has(y)){if(activeYears.size>1)activeYears.delete(y);}else{activeYears.add(y);}buildYearFilters();rebuildAll();};c.appendChild(chip);});
}
function buildMonthFilters(){
  const c=document.getElementById('monthFilters');c.innerHTML='';
  const allActive=allMonthNums.every(m=>activeMonths.has(m));
  c.appendChild(_makeToggleChip(allActive?'Clear':'All',allActive,'#a855f7',
    ()=>{allMonthNums.forEach(m=>activeMonths.add(m));buildMonthFilters();rebuildAll();},
    ()=>{activeMonths.clear();activeMonths.add(allMonthNums[0]);buildMonthFilters();rebuildAll();}));
  allMonthNums.forEach(m=>{
    const chip=document.createElement('div');chip.className='chip'+(activeMonths.has(m)?' active':'');
    chip.style.cssText=activeMonths.has(m)?'background:#a855f7;border-color:#a855f7;color:#fff':'background:var(--surface);border-color:#a855f7;color:var(--muted)';
    chip.textContent=MONTH_NAMES[parseInt(m)-1];
    chip.onclick=()=>{if(activeMonths.has(m)){if(activeMonths.size>1)activeMonths.delete(m);}else{activeMonths.add(m);}buildMonthFilters();rebuildAll();};
    c.appendChild(chip);
  });
}
function rebuildAll(){const _fm2=fm();const w=Math.max(window.innerWidth-80,_fm2.length*50);document.getElementById('costInner').style.minWidth=w+'px';chart.data=chartData();chart.update();updateCards();buildUnitCostChart();buildPieChart();buildYoY();buildMoMChart();buildCategoryCards();buildConsumptionChart();buildForecastChart();buildBillsList();buildMeterReadings();}
function buildFilters(){
  function makeChips(container){
    const allActive=vendors.every(v=>active.has(v));
    container.appendChild(_makeToggleChip(allActive?'Clear':'All',allActive,'#64748b',
      ()=>{vendors.forEach(v=>active.add(v));syncChips();_syncVendorToggles();rebuildAll();},
      ()=>{active.clear();active.add(vendors[0]);syncChips();_syncVendorToggles();rebuildAll();}));
    vendors.forEach(v=>{const chip=document.createElement('div');chip.className='chip'+(active.has(v)?' active':'');chip.dataset.vendor=v;chip.style.cssText=active.has(v)?`background:${COLORS[v]||'#888'};border-color:${COLORS[v]||'#888'}`:`background:var(--surface);border-color:${COLORS[v]||'#888'};color:var(--muted)`;chip.innerHTML=`${ICONS[v]||''} ${LABELS[v]||v}`;chip.onclick=()=>{if(active.has(v)){if(active.size>1)active.delete(v);}else{active.add(v);}syncChips();_syncVendorToggles();rebuildAll();};container.appendChild(chip);});
  }
  makeChips(document.getElementById('filters'));
}
function _syncVendorToggles(){
  const allActive=vendors.every(v=>active.has(v));
  document.querySelectorAll('#filters > .chip:first-child').forEach(t=>{
    t.textContent=allActive?'Clear':'All';t.className='chip'+(allActive?' active':'');
    t.style.cssText=allActive?'background:#64748b;border-color:#64748b;color:#fff':'background:var(--surface);border-color:#64748b;color:var(--muted)';
    t.onclick=allActive?()=>{active.clear();active.add(vendors[0]);syncChips();_syncVendorToggles();rebuildAll();}:()=>{vendors.forEach(v=>active.add(v));syncChips();_syncVendorToggles();rebuildAll();};
  });
}
function syncChips(){
  document.querySelectorAll('.chip[data-vendor]').forEach(chip=>{const v=chip.dataset.vendor;if(active.has(v)){chip.classList.add('active');chip.style.background=COLORS[v]||'#888';chip.style.borderColor=COLORS[v]||'#888';chip.style.color='#fff';}else{chip.classList.remove('active');chip.style.background='var(--surface)';chip.style.borderColor=COLORS[v]||'#888';chip.style.color='var(--muted)';}});
}
function chartData(){const _fm=fm();return{labels:_fm.map(fmtMonth),datasets:vendors.filter(v=>active.has(v)).map(v=>({label:v,_vendor:v,data:_fm.map(m=>(data[v]||{})[m]||0),backgroundColor:COLORS[v]||'#888',borderRadius:2}))};}
const barIconPlugin={id:'barIcons',afterDatasetsDraw(ch){const{ctx}=ch;ctx.save();ctx.textAlign='center';ctx.textBaseline='middle';ch.data.datasets.forEach((ds,di)=>{const meta=ch.getDatasetMeta(di);if(meta.hidden)return;const vk=ds._vendor||ds.label;const icon=ICONS[vk];if(!icon)return;meta.data.forEach((bar,i)=>{const h=Math.abs(bar.base-bar.y);if(h<18)return;const sz=Math.min(14,h-4);ctx.font=sz+'px sans-serif';ctx.fillText(icon,bar.x,(bar.y+bar.base)/2);});});ctx.restore();}};
let chart,pieChart,forecastChartObj,momChartObj;
function buildChart(){
  const _fm2=fm();const w=Math.max(window.innerWidth-80,_fm2.length*50);
  document.getElementById('costInner').style.minWidth=w+'px';
  const totalPlugin={id:'barTotals',afterDatasetsDraw(ch){const{ctx}=ch;ctx.save();ctx.font='bold 10px sans-serif';ctx.fillStyle='#94a3b8';ctx.textAlign='center';const meta=ch.getDatasetMeta(0);if(!meta||!meta.data)return;const nIdx=meta.data.length;for(let i=0;i<nIdx;i++){let sum=0;ch.data.datasets.forEach(ds=>{sum+=(ds.data[i]||0);});if(sum<=0)continue;const xPos=meta.data[i].x;let yPos=1e9;ch.data.datasets.forEach((_,di)=>{const m2=ch.getDatasetMeta(di);if(m2.hidden)return;const bar=m2.data[i];if(bar&&bar.y<yPos)yPos=bar.y;});ctx.fillText('\u20ac'+Math.round(sum),xPos,yPos-5);}ctx.restore();}};
  chart=new Chart(document.getElementById('chart').getContext('2d'),{type:'bar',data:chartData(),plugins:[totalPlugin,barIconPlugin],options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{mode:'index',callbacks:{label:c=>`${iLabel(c.dataset.label)}: \u20ac${c.parsed.y.toFixed(2)}`,footer:items=>`Total: \u20ac${items.reduce((a,b)=>a+b.parsed.y,0).toFixed(2)}`}}},scales:{x:{stacked:true,grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',font:{size:11}}},y:{stacked:true,grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',callback:v=>'\u20ac'+v}}}}});
}
function syncScrolls(){/* unitScroll removed in chart split; cost scroll is standalone */}
let unitElecChart,unitWaterChart,unitPelletsChart,unitGasKwhChart,unitGasM3Chart;
function buildUnitCostChart(){
  [unitElecChart,unitWaterChart,unitPelletsChart,unitGasKwhChart,unitGasM3Chart].forEach(c=>{if(c)c.destroy();});
  unitElecChart=unitWaterChart=unitPelletsChart=unitGasKwhChart=unitGasM3Chart=null;
  const elecRows=[],gasKwhRows=[],gasM3Rows=[],waterM3Rows=[],pelletRows=[];
  const _fm=fm();_fm.forEach(m=>{
    const elecEur=((data.electricity||{})[m]||0)+(ELEC_NET_BY_SVC[m]||0);
    const eKwh=CONSUMPTION.elec_kwh[m];
    const gasEur=((data.gas||{})[m]||0)+(GAS_NET_BY_SVC[m]||0);
    const gKwh=CONSUMPTION.gas_kwh[m];const gM3=CONSUMPTION.gas_m3[m];
    const waterEur=WATER_EUR_BY_SVC[m]||0;
    const wM3=CONSUMPTION.water_m3[m];
    const hasPellet=(data.pellets||{})[m];
    elecRows.push(eKwh?(elecEur/eKwh*100):null);
    gasKwhRows.push(gKwh?(gasEur/gKwh*100):null);
    gasM3Rows.push(gM3?(gasEur/gM3*100):null);
    waterM3Rows.push(wM3?(waterEur/wM3):null);
    pelletRows.push(hasPellet?PELLET_S_KWH:null);
  });
  const labels=_fm.map(fmtMonth);
  const mkOpts=(title,unit,fmt)=>({responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},title:{display:true,text:title,color:'#94a3b8',font:{size:12,weight:'600'}},tooltip:{callbacks:{label:c=>c.parsed.y==null?'\u2014':fmt(c.parsed.y)}}},scales:{x:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',font:{size:11}}},y:{ticks:{color:'#94a3b8',callback:unit},grid:{color:'rgba(255,255,255,.05)'}}}});
  const fmtSents=v=>v.toFixed(2)+' s';const fmtEur=v=>'\u20ac'+v.toFixed(2);
  const sentsAxis=v=>v+' s';const eurAxis=v=>'\u20ac'+v.toFixed(2);
  unitElecChart=new Chart(document.getElementById('unitElecChart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Electricity s/kWh',data:elecRows,borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,.15)',fill:true,tension:0.3,pointRadius:3},
  ]},options:mkOpts('\u26a1 Electricity (s/kWh)',sentsAxis,fmtSents)});
  unitWaterChart=new Chart(document.getElementById('unitWaterChart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Water \u20ac/m\u00b3',data:waterM3Rows,borderColor:'#06b6d4',backgroundColor:'rgba(6,182,212,.15)',fill:true,tension:0.3,pointRadius:3},
  ]},options:{...mkOpts('💧 Water (\u20ac/m\u00b3)',eurAxis,fmtEur),scales:{x:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',font:{size:11}}},y:{suggestedMin:0,suggestedMax:6,ticks:{color:'#94a3b8',callback:eurAxis},grid:{color:'rgba(255,255,255,.05)'}}}}});
  unitPelletsChart=new Chart(document.getElementById('unitPelletsChart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Pellets s/kWh',data:pelletRows,borderColor:'#ef4444',backgroundColor:'rgba(239,68,68,.15)',fill:true,tension:0,pointRadius:3,borderDash:[6,3]},
  ]},options:mkOpts('🪵 Pellets (s/kWh, season avg)',sentsAxis,fmtSents)});
  unitGasKwhChart=new Chart(document.getElementById('unitGasKwhChart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Gas s/kWh',data:gasKwhRows,borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.15)',fill:true,tension:0.3,pointRadius:3},
  ]},options:mkOpts('🔥 Gas (s/kWh)',sentsAxis,fmtSents)});
  unitGasM3Chart=new Chart(document.getElementById('unitGasM3Chart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Gas s/m\u00b3',data:gasM3Rows,borderColor:'#93c5fd',backgroundColor:'rgba(147,197,253,.15)',fill:true,tension:0.3,pointRadius:3},
  ]},options:mkOpts('🔥 Gas (s/m\u00b3)',sentsAxis,fmtSents)});
}

/* ── Pie Chart: Spending by Category ── */
function buildPieChart(){
  if(pieChart){pieChart.destroy();pieChart=null;}
  const _fm=fm();
  const totals=vendors.filter(v=>active.has(v)).map(v=>{let s=0;_fm.forEach(m=>s+=(data[v]||{})[m]||0);return{v,s};}).filter(x=>x.s>0).sort((a,b)=>b.s-a.s);
  const pieIconPlugin={id:'pieIcons',afterDraw(ch){const{ctx}=ch;const meta=ch.getDatasetMeta(0);if(!meta||!meta.data)return;ctx.save();ctx.textAlign='center';ctx.textBaseline='middle';meta.data.forEach((arc,i)=>{const v=totals[i].v;const icon=ICONS[v];if(!icon)return;const angle=(arc.startAngle+arc.endAngle)/2;const r=(arc.innerRadius+arc.outerRadius)/2;const x=arc.x+Math.cos(angle)*r;const y=arc.y+Math.sin(angle)*r;const span=arc.endAngle-arc.startAngle;if(span<0.3)return;const sz=Math.min(16,span*20);ctx.font=sz+'px sans-serif';ctx.fillText(icon,x,y);});ctx.restore();}};
  const pieCenterPlugin={id:'pieCenter',afterDraw(ch){const grandTotal=ch.data.datasets[0].data.reduce((a,b)=>a+b,0);const{ctx,chartArea:{left,right,top,bottom}}=ch;const cx=(left+right)/2;const cy=(top+bottom)/2;ctx.save();ctx.textAlign='center';ctx.textBaseline='middle';ctx.font='bold 18px system-ui,sans-serif';ctx.fillStyle='#e2e8f0';ctx.fillText('\u20ac'+Math.round(grandTotal),cx,cy-8);ctx.font='11px system-ui,sans-serif';ctx.fillStyle='#64748b';ctx.fillText('total',cx,cy+10);ctx.restore();}};
  pieChart=new Chart(document.getElementById('pieChart').getContext('2d'),{type:'doughnut',data:{
    labels:totals.map(t=>iLabel(t.v)),
    datasets:[{data:totals.map(t=>Math.round(t.s*100)/100),backgroundColor:totals.map(t=>COLORS[t.v]||'#888'),borderWidth:0}]
  },plugins:[pieIconPlugin,pieCenterPlugin],options:{responsive:true,maintainAspectRatio:false,cutout:'55%',plugins:{legend:{position:'right',labels:{color:'#94a3b8',padding:10,font:{size:11}}},tooltip:{callbacks:{label:c=>{const total=c.dataset.data.reduce((a,b)=>a+b,0);return iLabel(totals[c.dataIndex].v)+': \u20ac'+c.parsed.toFixed(2)+' ('+(c.parsed/total*100).toFixed(1)+'%)';}}}}}});
}

/* ── Year-over-Year Table ── */
function buildYoY(){
  const _fm=fm();
  const yearTotals={};const yearMonths={};
  _fm.forEach(m=>{const y=m.slice(0,4);if(!yearTotals[y])yearTotals[y]=0;if(!yearMonths[y])yearMonths[y]=0;yearMonths[y]++;vendors.filter(v=>active.has(v)).forEach(v=>yearTotals[y]+=(data[v]||{})[m]||0);});
  const years=Object.keys(yearTotals).sort();
  let html='';let prev=null;
  years.forEach(y=>{
    const t=yearTotals[y];const avg=t/yearMonths[y];
    let changeHtml='\u2014';
    if(prev!==null&&prev>0){const pct=((t-prev)/prev*100);changeHtml=`<span class="${pct>0?'change-up':'change-down'}">${pct>0?'+':''}${pct.toFixed(1)}%</span>`;}
    html+=`<tr><td>${y}</td><td class="amount">\u20ac${t.toFixed(0)}</td><td class="amount">${changeHtml}</td><td class="amount">\u20ac${avg.toFixed(0)}/mo</td></tr>`;
    prev=t;
  });
  document.getElementById('yoyBody').innerHTML=html;
}

/* ── Month-over-Month Comparison (stacked by vendor per year-group) ── */
function buildMoMChart(){
  if(momChartObj){momChartObj.destroy();momChartObj=null;}
  const activeMonthsSorted=[...activeMonths].sort();
  const yearsUsed=[...activeYears].sort();
  const labels=[];const groupKeys=[];
  activeMonthsSorted.forEach(mo=>{
    yearsUsed.forEach(y=>{labels.push(MONTH_NAMES[parseInt(mo)-1]+" '"+y.slice(2));groupKeys.push(y+'-'+mo);});
  });
  const activeVendors=vendors.filter(v=>active.has(v));
  const datasets=activeVendors.map(v=>({
    label:iLabel(v),_vendor:v,
    data:groupKeys.map(k=>Math.round(((data[v]||{})[k]||0)*100)/100),
    backgroundColor:COLORS[v]||'#888',
    borderRadius:2,
    stack:'s',
  }));
  const w=Math.max(400,groupKeys.length*45);
  momChartObj=new Chart(document.getElementById('momChart').getContext('2d'),{type:'bar',data:{labels,datasets},plugins:[barIconPlugin],options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#94a3b8',font:{size:11}}},tooltip:{mode:'index',callbacks:{label:c=>{const v=c.parsed.y;return v?'  '+iLabel(c.dataset.label)+': \u20ac'+v.toFixed(2):'';},footer:items=>{const total=items.reduce((a,b)=>a+b.parsed.y,0);return total?'Total: \u20ac'+total.toFixed(2):'';}}}},scales:{x:{stacked:true,grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',font:{size:10},maxRotation:45}},y:{stacked:true,grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',callback:v=>'\u20ac'+v}}}}});
  document.getElementById('momInner').style.minWidth=w+'px';
}

/* ── Category Breakdown Cards (monthly avg per category) ── */
function buildCategoryCards(){
  const _fm=fm();const n=_fm.length||1;
  const el=document.getElementById('categoryCards');el.innerHTML='';
  const items=vendors.filter(v=>active.has(v)).map(v=>{let s=0;_fm.forEach(m=>s+=(data[v]||{})[m]||0);return{v,total:s,avg:s/n};}).filter(x=>x.total>0).sort((a,b)=>b.total-a.total);
  items.forEach(it=>{
    const label=iLabel(it.v);const color=COLORS[it.v]||'#888';
    el.insertAdjacentHTML('beforeend',`<div class="card" style="border-left:3px solid ${color}"><div class="card-label">${label}</div><div class="card-value">\u20ac${it.avg.toFixed(0)}<span style="font-size:0.6em;color:var(--muted)">/mo</span></div><div class="card-sub">\u20ac${it.total.toFixed(0)} total over ${_fm.length} months</div></div>`);
  });
}

/* ── Consumption Trends (usage, not cost) ── */
let consElecChart,consGasChart,consWaterChart;
function buildConsumptionChart(){
  [consElecChart,consGasChart,consWaterChart].forEach(c=>{if(c)c.destroy();});
  consElecChart=consGasChart=consWaterChart=null;
  const _fm=fm();
  const eKwh=[],gM3=[],wM3=[];
  _fm.forEach(m=>{
    eKwh.push(CONSUMPTION.elec_kwh[m]||null);
    gM3.push(CONSUMPTION.gas_m3[m]||null);
    wM3.push(CONSUMPTION.water_m3[m]||null);
  });
  const labels=_fm.map(fmtMonth);
  const baseScales={x:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',font:{size:11}}}};
  const baseOpts={responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#94a3b8',font:{size:11}}},title:{display:true,color:'#94a3b8',font:{size:12,weight:'600'}}}};
  consElecChart=new Chart(document.getElementById('consElecChart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Electricity (kWh)',data:eKwh,borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,.15)',fill:true,tension:0.3,pointRadius:3},
  ]},options:{...baseOpts,plugins:{...baseOpts.plugins,title:{...baseOpts.plugins.title,text:'\u26a1 Electricity (kWh)'},tooltip:{callbacks:{label:c=>c.parsed.y==null?c.dataset.label+': \u2014':c.dataset.label+': '+c.parsed.y.toFixed(1)}}},scales:{...baseScales,y:{ticks:{color:'#94a3b8',callback:v=>v+' kWh'},grid:{color:'rgba(255,255,255,.05)'}}}}});
  consWaterChart=new Chart(document.getElementById('consWaterChart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Water (m\u00b3)',data:wM3,borderColor:'#06b6d4',backgroundColor:'rgba(6,182,212,.15)',fill:true,tension:0.3,pointRadius:3},
  ]},options:{...baseOpts,plugins:{...baseOpts.plugins,title:{...baseOpts.plugins.title,text:'💧 Water (m\u00b3)'},tooltip:{callbacks:{label:c=>c.parsed.y==null?c.dataset.label+': \u2014':c.dataset.label+': '+c.parsed.y.toFixed(2)}}},scales:{...baseScales,y:{ticks:{color:'#94a3b8',callback:v=>v+' m\u00b3'},grid:{color:'rgba(255,255,255,.05)'}}}}});
  consGasChart=new Chart(document.getElementById('consGasChart').getContext('2d'),{type:'line',data:{labels,datasets:[
    {label:'Gas (m\u00b3)',data:gM3,borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.15)',fill:true,tension:0.3,pointRadius:3},
  ]},options:{...baseOpts,plugins:{...baseOpts.plugins,title:{...baseOpts.plugins.title,text:'🔥 Gas (m\u00b3)'},tooltip:{callbacks:{label:c=>c.parsed.y==null?c.dataset.label+': \u2014':c.dataset.label+': '+c.parsed.y.toFixed(1)}}},scales:{...baseScales,y:{ticks:{color:'#94a3b8',callback:v=>v+' m\u00b3'},grid:{color:'rgba(255,255,255,.05)'}}}}});
}

/* ── Budget Forecast ── */
function buildForecastChart(){
  if(forecastChartObj){forecastChartObj.destroy();forecastChartObj=null;}
  const _fm=fm();if(_fm.length<3)return;
  /* Clamp to last fully-completed month — prepaid bills (e.g. annual insurance) leak
     forward-allocated entries into future service months; treating those as "actual"
     drags the rolling average and offsets the forecast horizon. */
  const _now=new Date();
  const _cutoff=`${_now.getFullYear()}-${String(_now.getMonth()+1).padStart(2,'0')}`;
  const completed=_fm.filter(m=>m<_cutoff);
  if(completed.length<3)return;
  /* Calculate monthly totals */
  const monthlyTotals=completed.map(m=>{let s=0;vendors.filter(v=>active.has(v)).forEach(v=>s+=(data[v]||{})[m]||0);return s;});
  /* Rolling 6-month average for forecast */
  const window6=Math.min(6,monthlyTotals.length);
  const recent=monthlyTotals.slice(-window6);
  const avg=recent.reduce((a,b)=>a+b,0)/window6;
  const stdDev=Math.sqrt(recent.reduce((a,b)=>a+(b-avg)**2,0)/window6);
  /* Generate 3 forecast months starting from the cutoff month (current month) */
  const lastMonth=completed[completed.length-1];
  const [ly,lm]=[parseInt(lastMonth.slice(0,4)),parseInt(lastMonth.slice(5,7))];
  const forecastMonths=[];const forecastLabels=[];
  for(let i=1;i<=3;i++){
    let fy=ly,fmo=lm+i;if(fmo>12){fmo-=12;fy++;}
    forecastMonths.push(`${fy}-${String(fmo).padStart(2,'0')}`);
    forecastLabels.push(fmtMonth(`${fy}-${String(fmo).padStart(2,'0')}`));
  }
  const allLabels=completed.slice(-12).map(fmtMonth).concat(forecastLabels);
  const actualData=monthlyTotals.slice(-12);
  const forecastData=new Array(actualData.length).fill(null).concat([avg,avg,avg]);
  const upperBand=new Array(actualData.length).fill(null).concat([avg+stdDev,avg+stdDev,avg+stdDev]);
  const lowerBand=new Array(actualData.length).fill(null).concat([Math.max(0,avg-stdDev),Math.max(0,avg-stdDev),Math.max(0,avg-stdDev)]);
  const actualFull=actualData.concat([null,null,null]);
  const avgLine=new Array(allLabels.length).fill(avg);

  forecastChartObj=new Chart(document.getElementById('forecastChart').getContext('2d'),{type:'line',data:{labels:allLabels,datasets:[
    {label:'Actual',data:actualFull,borderColor:'#8b5cf6',backgroundColor:'rgba(139,92,246,.15)',fill:false,tension:0.3,pointRadius:4,borderWidth:2},
    {label:'Forecast',data:forecastData,borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,.15)',fill:false,tension:0,pointRadius:6,borderWidth:2,borderDash:[6,3],pointStyle:'triangle'},
    {label:'Upper band (+1\u03c3)',data:upperBand,borderColor:'rgba(239,68,68,.3)',backgroundColor:'rgba(239,68,68,.05)',fill:'+1',tension:0,pointRadius:0,borderWidth:1,borderDash:[3,3]},
    {label:'Lower band (-1\u03c3)',data:lowerBand,borderColor:'rgba(34,197,94,.3)',backgroundColor:'rgba(34,197,94,.05)',fill:false,tension:0,pointRadius:0,borderWidth:1,borderDash:[3,3]},
    {label:'6-mo avg',data:avgLine,borderColor:'rgba(148,163,184,.3)',backgroundColor:'transparent',fill:false,tension:0,pointRadius:0,borderWidth:1,borderDash:[2,4]},
  ]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#94a3b8',font:{size:11}}},tooltip:{callbacks:{label:c=>{const v=c.parsed.y;if(v==null)return '';return c.dataset.label+': \u20ac'+v.toFixed(0);}}}},scales:{x:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',font:{size:11}}},y:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',callback:v=>'\u20ac'+v}}}}});
}

/* ── Bills by Month (card-style list) ── */
function buildBillsList(){
  const _fm=fm();const last36=_fm.slice(-36);const last36Set=new Set(last36);
  const rows=BILL_FILES.filter(r=>last36Set.has(r.m)&&active.has(r.v));
  rows.sort((a,b)=>b.m.localeCompare(a.m)||b.a-a.a);
  const el=document.getElementById('billsList');el.innerHTML='';
  let curMonth='';
  rows.forEach(r=>{
    if(r.m!==curMonth){curMonth=r.m;el.insertAdjacentHTML('beforeend',`<div class="bill-month-header">${fmtMonth(r.m)}</div>`);}
    const icon=ICONS[r.v]||'';const color=COLORS[r.v]||'#888';const label=LABELS[r.v]||r.v;
    const _tok=(window.__DT)||new URLSearchParams(location.search).get('token')||'';const _q=_tok?`?token=${encodeURIComponent(_tok)}`:'';const pdfHtml=(r.f&&BASE_URL)?`<a class="pdf-link" href="${BASE_URL}/bills/${encodeURIComponent(r.f)}${_q}" target="_blank">📄 PDF</a>`:'';
    el.insertAdjacentHTML('beforeend',`<div class="bill-item"><div class="bill-icon" style="background:${color}22">${icon}</div><div class="bill-info"><div class="name">${label}</div><div class="date">${r.m}</div></div><div class="bill-amount-col"><div class="eur">\u20ac${r.a.toFixed(2)}</div>${pdfHtml}</div></div>`);
  });
}

/* ── Meter Readings (cards + sparklines) ── */
let meterElecChart,meterGasChart,meterWaterChart;
function buildMeterReadings(){
  const el=document.getElementById('meterCards');el.innerHTML='';
  const _fm=fm();
  const types=[
    {key:'elec_kwh',label:'Electricity',icon:'\u26a1',color:'#f59e0b',unit:'kWh',chartId:'meterElecChart'},
    {key:'gas_m3',label:'Gas',icon:'\uD83D\uDD25',color:'#3b82f6',unit:'m\u00b3',chartId:'meterGasChart'},
    {key:'water_m3',label:'Water',icon:'\uD83D\uDCA7',color:'#06b6d4',unit:'m\u00b3',chartId:'meterWaterChart'},
  ];
  types.forEach(t=>{
    const cdata=CONSUMPTION[t.key]||{};
    const mths=_fm.filter(m=>cdata[m]!=null);
    if(mths.length===0)return;
    const lastM=mths[mths.length-1];const prevM=mths.length>=2?mths[mths.length-2]:null;
    const lastVal=cdata[lastM];const prevVal=prevM?cdata[prevM]:null;
    let deltaHtml='';
    if(prevVal!=null){const diff=lastVal-prevVal;const pct=prevVal>0?((diff/prevVal)*100):0;
      const cls=diff>0?'up':'down';
      deltaHtml=`<div class="meter-delta ${cls}">${diff>0?'\u25b2':'\u25bc'} ${Math.abs(diff).toFixed(1)} ${t.unit} (${Math.abs(pct).toFixed(1)}%) vs ${fmtMonth(prevM)}</div>`;}
    el.insertAdjacentHTML('beforeend',`<div class="meter-card"><div class="meter-header"><div><div class="meter-title">${t.icon} ${t.label}</div><div class="meter-date">Latest: ${fmtMonth(lastM)}</div>${deltaHtml}</div><div style="text-align:right"><div class="meter-value-big">${lastVal.toFixed(1)}</div><div class="meter-unit">${t.unit} (month)</div></div></div></div>`);
  });
  /* Build sparkline charts */
  [meterElecChart,meterGasChart,meterWaterChart].forEach(c=>{if(c){c.destroy();}});
  meterElecChart=null;meterGasChart=null;meterWaterChart=null;
  types.forEach(t=>{
    const cdata=CONSUMPTION[t.key]||{};
    const mths=_fm.filter(m=>cdata[m]!=null).slice(-12);
    if(mths.length<2)return;
    const canvas=document.getElementById(t.chartId);if(!canvas)return;
    const ch=new Chart(canvas.getContext('2d'),{type:'line',data:{labels:mths.map(fmtMonth),datasets:[{label:t.label+' ('+t.unit+')',data:mths.map(m=>cdata[m]),borderColor:t.color,backgroundColor:t.color+'20',fill:true,tension:0.35,pointRadius:3,borderWidth:2}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#94a3b8',font:{size:11}}}},scales:{x:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',font:{size:10}}},y:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#94a3b8',callback:v=>v+' '+t.unit}}}}});
    if(t.chartId==='meterElecChart')meterElecChart=ch;
    if(t.chartId==='meterGasChart')meterGasChart=ch;
    if(t.chartId==='meterWaterChart')meterWaterChart=ch;
  });
}

buildYearFilters();buildMonthFilters();updateCards();buildFilters();buildChart();buildUnitCostChart();syncScrolls();buildPieChart();buildYoY();buildMoMChart();buildCategoryCards();buildConsumptionChart();buildForecastChart();buildBillsList();buildMeterReadings();
</script></body></html>
"""
