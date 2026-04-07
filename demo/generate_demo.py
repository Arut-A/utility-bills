"""
generate_demo.py
================
Generates demo/dashboard_demo.html with realistic but entirely fake bill data.

Run from the repo root:
    python demo/generate_demo.py

Output: demo/dashboard_demo.html — a fully functional, self-contained copy of
the dashboard that can be opened directly in a browser. No server or database
needed.

All amounts, dates, and consumption figures are invented. No real financial
data is included.
"""

import json
import os
import sys
import random

# ---------------------------------------------------------------------------
# Fake data configuration
# Adjust these to change the look of the demo dashboard.
# ---------------------------------------------------------------------------

MONTHS = [f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)]

# Seed for reproducible output
random.seed(42)

def seasonal(base, winter_factor, month):
    """Scale a base amount by a seasonal factor — higher in winter months."""
    m = int(month[5:7])
    winter = [11, 12, 1, 2, 3]
    shoulder = [4, 10]
    if m in winter:
        return base * winter_factor
    if m in shoulder:
        return base * (1 + (winter_factor - 1) * 0.5)
    return base

def jitter(val, pct=0.08):
    """Add ±pct% random noise to a value."""
    return round(val * (1 + random.uniform(-pct, pct)), 2)


# --- Cost data (EUR/month) ---

bill_data = {
    "electricity": {
        m: jitter(seasonal(22, 1.7, m)) for m in MONTHS
    },
    "electricity_network": {
        m: jitter(seasonal(14, 1.3, m)) for m in MONTHS
    },
    "gas": {
        m: jitter(seasonal(28, 2.8, m)) for m in MONTHS
    },
    "gas_network": {
        m: jitter(seasonal(10, 1.5, m)) for m in MONTHS
    },
    "water": {
        m: jitter(18) for m in MONTHS
    },
    "phone": {
        m: jitter(22, 0.02) for m in MONTHS
    },
    "internet": {
        m: jitter(31, 0.01) for m in MONTHS
    },
    "home_security": {
        m: jitter(16, 0.01) for m in MONTHS
    },
    "garbage": {
        m: jitter(14, 0.03) for m in MONTHS
    },
    "pellets": {
        # Heating season Sep–Apr only, spread evenly
        m: jitter(52) if int(m[5:7]) in (9, 10, 11, 12, 1, 2, 3, 4) else 0.0
        for m in MONTHS
    },
    "house_insurance": {
        m: jitter(26, 0.01) for m in MONTHS
    },
}

# Remove zero-value pellet months
bill_data["pellets"] = {m: v for m, v in bill_data["pellets"].items() if v > 0}

# --- Consumption data ---

elec_kwh = {
    m: round(jitter(seasonal(140, 1.8, m), 0.1)) for m in MONTHS
}
gas_m3 = {
    m: round(jitter(seasonal(55, 3.0, m), 0.12), 1)
    for m in MONTHS if int(m[5:7]) in (9, 10, 11, 12, 1, 2, 3, 4)
}
gas_kwh = {m: round(v * 10.55, 1) for m, v in gas_m3.items()}
water_m3 = {m: round(jitter(6.5, 0.15), 1) for m in MONTHS}

elec_net_svc = {m: bill_data["electricity_network"].get(m, 0) for m in MONTHS}
gas_net_svc = {m: bill_data["gas_network"].get(m, 0) for m in MONTHS}
water_eur_svc = {m: bill_data["water"].get(m, 0) for m in MONTHS}

# Lifetime pellet totals
pellet_total_kg = 4800.0
pellet_total_eur = sum(bill_data["pellets"].values())
PELLET_KWH_PER_KG = 4.8

# Bill files table (no real PDFs — links disabled)
bill_files = [
    {"v": vendor, "m": month, "a": round(amt, 2), "f": None}
    for vendor, months_data in bill_data.items()
    for month, amt in months_data.items()
    if amt > 0
]

# RAW structure for the JS
all_vendors = sorted(bill_data.keys())
all_months = sorted(set(m for d in bill_data.values() for m in d))
raw_json = {
    "vendors": all_vendors,
    "months": all_months,
    "data": {v: {m: round(a, 2) for m, a in d.items()} for v, d in bill_data.items()},
}

# ---------------------------------------------------------------------------
# Colors, labels, icons
# ---------------------------------------------------------------------------

COLORS = {
    'electricity': '#f59e0b', 'electricity_network': '#fcd34d',
    'gas': '#3b82f6', 'gas_network': '#93c5fd',
    'water': '#06b6d4', 'house_insurance': '#14b8a6',
    'internet': '#10b981', 'home_security': '#10b981',
    'phone': '#8b5cf6', 'garbage': '#6b7280', 'pellets': '#ef4444',
}
LABELS = {
    'electricity': 'Electricity', 'electricity_network': 'Electricity Network',
    'gas': 'Gas', 'gas_network': 'Gas Network', 'water': 'Water',
    'house_insurance': 'House Insurance', 'internet': 'Internet',
    'home_security': 'Home Security', 'phone': 'Phone',
    'garbage': 'Garbage', 'pellets': 'Pellets',
}
ICONS = {
    'electricity': '\u26a1', 'electricity_network': '\U0001f50c',
    'gas': '\U0001f525', 'gas_network': '\U0001f3ed',
    'water': '\U0001f4a7', 'phone': '\U0001f4f1', 'internet': '\U0001f310',
    'home_security': '\U0001f512', 'garbage': '\U0001f5d1\ufe0f',
    'pellets': '\U0001fab5', 'house_insurance': '\U0001f3e0',
}

# ---------------------------------------------------------------------------
# Load the HTML template from the dashboard generator
# ---------------------------------------------------------------------------

script_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(script_dir)
sys.path.insert(0, repo_root)

try:
    src = open(os.path.join(repo_root, "dashboard_generator_patched.py"), encoding="utf-8").read()
    # Extract _TEMPLATE_TOP and _TEMPLATE_BOTTOM by finding their raw string assignments
    import re as _re
    _top_match = _re.search(r'_TEMPLATE_TOP\s*=\s*r"""(.*?)"""', src, _re.DOTALL)
    _bot_match = _re.search(r'_TEMPLATE_BOTTOM\s*=\s*r"""(.*?)"""', src, _re.DOTALL)
    if not _top_match or not _bot_match:
        raise ValueError("Could not locate _TEMPLATE_TOP or _TEMPLATE_BOTTOM in source file")
    _TEMPLATE_TOP = _top_match.group(1)
    _TEMPLATE_BOTTOM = _bot_match.group(1)
except Exception as e:
    print(f"Error loading templates from dashboard_generator_patched.py: {e}")
    print("Make sure you run this script from the repo root:")
    print("  python demo/generate_demo.py")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Assemble the HTML
# ---------------------------------------------------------------------------

html = _TEMPLATE_TOP
html += "const RAW = " + json.dumps(raw_json, ensure_ascii=False) + ";\n"
html += "const CONSUMPTION = " + json.dumps({
    "elec_kwh": {m: round(v, 3) for m, v in sorted(elec_kwh.items())},
    "gas_m3":   {m: round(v, 3) for m, v in sorted(gas_m3.items())},
    "gas_kwh":  {m: round(v, 3) for m, v in sorted(gas_kwh.items())},
    "water_m3": {m: round(v, 3) for m, v in sorted(water_m3.items())},
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
html += "const BASE_URL = '';\n"
html += "const COLORS = " + json.dumps(COLORS, ensure_ascii=False) + ";\n"
html += "const LABELS = " + json.dumps(LABELS, ensure_ascii=False) + ";\n"
html += "const ICONS = " + json.dumps(ICONS, ensure_ascii=False) + ";\n"
html += _TEMPLATE_BOTTOM

out_path = os.path.join(script_dir, "dashboard_demo.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Generated: {out_path}")
print(f"  {len(all_vendors)} vendors, {len(all_months)} months")
print(f"  Open in browser: file://{out_path}")
