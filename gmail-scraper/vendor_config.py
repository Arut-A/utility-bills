"""
vendor_config.py
Shared config loader for vendors.yaml — used by bill-parser and gmail-scraper.
Place this file in both /app/ directories.
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("vendor_config")

CONFIG_PATH = Path(os.environ.get("VENDOR_CONFIG_PATH", "/data/config/vendors.yaml"))

_cache: dict = {"mtime": 0.0, "data": None, "compiled": {}}


def load_config() -> dict:
    """Load and cache vendors.yaml, refreshing if the file changed on disk."""
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except FileNotFoundError:
        log.error("Vendor config not found: %s", CONFIG_PATH)
        return {"gmail": {}, "vendors": {}}
    if _cache["mtime"] < mtime or _cache["data"] is None:
        with open(CONFIG_PATH) as f:
            _cache["data"] = yaml.safe_load(f)
        _cache["mtime"] = mtime
        _cache["compiled"] = {}  # clear compiled regex cache
        log.info("Loaded vendor config (%d vendors)", len(_cache["data"].get("vendors", {})))
    return _cache["data"]


def get_vendors() -> dict:
    return load_config().get("vendors", {})


def get_gmail_config() -> dict:
    return load_config().get("gmail", {})


# ── Compiled regex cache ─────────────────────────────────────────────────────

def _compile(pattern: str) -> re.Pattern:
    """Compile and cache a regex pattern."""
    if pattern not in _cache["compiled"]:
        _cache["compiled"][pattern] = re.compile(pattern, re.IGNORECASE)
    return _cache["compiled"][pattern]


# ── Lookup helpers ───────────────────────────────────────────────────────────

def get_dashboard_colors() -> dict:
    return {k: v["dashboard"]["color"]
            for k, v in get_vendors().items()
            if "dashboard" in v}


def get_dashboard_labels() -> dict:
    return {k: v["dashboard"]["label"]
            for k, v in get_vendors().items()
            if "dashboard" in v}


def get_provider_names() -> dict:
    """Map vendor_slug -> provider name for Telegram summaries."""
    return {k: v.get("provider", v.get("display_name", k))
            for k, v in get_vendors().items()}


def get_gmail_sender_domains() -> list:
    """Collect all sender domains/names from all vendors for Gmail query."""
    domains = []
    for vc in get_vendors().values():
        for d in vc.get("gmail", {}).get("sender_domains", []):
            if d not in domains:
                domains.append(d)
    return domains


def get_gmail_labels() -> dict:
    """Return {vendor_slug: label_name} for vendors that use label-based search."""
    return {k: v["gmail"]["label"]
            for k, v in get_vendors().items()
            if v.get("gmail", {}).get("label")}


def get_no_pdf_vendors() -> dict:
    """Return {vendor_slug: config} for vendors with no_pdf_variant."""
    return {k: v for k, v in get_vendors().items()
            if v.get("special", {}).get("no_pdf_variant")}



def get_vendor_gmail_label(vendor_slug: str) -> str | None:
    """Return the Gmail label name for a given vendor_category, or None."""
    vc = get_vendors().get(vendor_slug, {})
    return vc.get("gmail", {}).get("gmail_label")


# ── Classification ───────────────────────────────────────────────────────────

def classify_vendor(pdf_path: str, text: str) -> str:
    """
    Classify a bill by vendor using config-driven rules.
    Priority:
      1. Filename slug match (with require/exclude keyword disambiguation)
      2. Text keyword fallback
    """
    filename = Path(pdf_path).name.lower()
    content = text[:3000].lower()
    combined = filename + " " + content
    vendors = get_vendors()

    # Pass 1: filename slug match — collect all matches, pick best
    candidates = []
    for vendor_slug, vc in vendors.items():
        cls = vc.get("classification", {})
        for slug in cls.get("filename_slugs", []):
            if slug in filename:
                candidates.append((vendor_slug, vc))
                break

    # Disambiguate candidates using require/exclude keywords
    if candidates:
        for vendor_slug, vc in candidates:
            cls = vc.get("classification", {})
            require = cls.get("require_keywords", [])
            exclude = cls.get("exclude_keywords", [])

            # If require_keywords set, text must contain at least one
            if require and not any(kw in content for kw in require):
                continue
            # If exclude_keywords set, text must not contain any
            if exclude and any(kw in content for kw in exclude):
                continue
            return vendor_slug

        # If all candidates were filtered out by require/exclude,
        # try the first candidate with require_keywords (it's the "else" branch)
        # e.g., Alexela filename matches both electricity and gas;
        # if gas keywords not found, fall through to electricity
        for vendor_slug, vc in candidates:
            cls = vc.get("classification", {})
            if not cls.get("require_keywords"):
                exclude = cls.get("exclude_keywords", [])
                if not (exclude and any(kw in content for kw in exclude)):
                    return vendor_slug

    # Pass 2: text keyword fallback
    best_slug = "unknown"
    best_score = 0
    for vendor_slug, vc in vendors.items():
        cls = vc.get("classification", {})
        keywords = cls.get("text_keywords", [])
        if not keywords:
            continue
        score = sum(1 for kw in keywords if kw in combined)
        if score > best_score:
            require = cls.get("require_keywords", [])
            exclude = cls.get("exclude_keywords", [])
            if require and not any(kw in content for kw in require):
                continue
            if exclude and any(kw in content for kw in exclude):
                continue
            best_score = score
            best_slug = vendor_slug

    return best_slug


# ── Total extraction ─────────────────────────────────────────────────────────

def extract_total(vendor_slug: str, text: str) -> Optional[float]:
    """Extract total amount using vendor-specific regex patterns from config."""
    vc = get_vendors().get(vendor_slug, {})
    for pattern in vc.get("parsing", {}).get("total_patterns", []):
        m = _compile(pattern).search(text)
        if m:
            val = float(m.group(1).replace(",", "."))
            if val > 0:
                return val
    return None


# ── Consumption extraction ───────────────────────────────────────────────────

def _parse_num(s: str) -> float:
    return float(s.replace(" ", "").replace(",", "."))


def extract_consumption(vendor_slug: str, text: str) -> dict:
    """Extract consumption data (kWh, m3, kg, etc.) and billing period from config."""
    vc = get_vendors().get(vendor_slug, {})
    parsing = vc.get("parsing", {})
    result = {}

    # ── Consumption units ────────────────────────────────────────────
    cons = parsing.get("consumption")
    if cons:
        field = cons["field"]
        found = False

        # Primary patterns
        for pattern in cons.get("patterns", []):
            m = re.search(pattern, text)
            if m:
                result[field] = _parse_num(m.group(1))
                found = True
                break

        # Multi-value: sum multiple matches after a context keyword
        if not found and cons.get("multi_value_context"):
            ctx = cons["multi_value_context"]
            idx = text.find(ctx)
            if idx >= 0:
                block = text[idx:idx + 500]
                pat = cons.get("multi_value_pattern", "")
                if pat:
                    vals = re.findall(pat, block, re.MULTILINE)
                    limit = cons.get("multi_value_limit", 10)
                    if vals:
                        result[field] = sum(float(v) for v in vals[:limit])
                        found = True

                # Gas-specific: separate m3 and kWh patterns in the block
                for key_suffix in ["m3", "kwh"]:
                    mvp = cons.get(f"multi_value_pattern_{key_suffix}")
                    if mvp:
                        vals = re.findall(mvp, block)
                        if vals:
                            target = "gas_m3" if key_suffix == "m3" else "energy_kwh"
                            result[target] = float(vals[0])

        # Secondary consumption (e.g., gas kWh alongside m3)
        secondary = cons.get("secondary")
        if secondary:
            for pattern in secondary.get("patterns", []):
                m = re.search(pattern, text)
                if m:
                    result[secondary["field"]] = _parse_num(m.group(1))
                    break

        # Set unit_type if specified
        if cons.get("unit_type") and field in result:
            result["unit_type"] = cons["unit_type"]

    # ── Billing period ───────────────────────────────────────────────
    bp = parsing.get("billing_period", {})

    # Standard pattern: two dates in one regex
    for pattern in bp.get("patterns", []):
        m = re.search(pattern, text)
        if m:
            result["billing_period_start"] = _parse_date(m.group(1))
            result["billing_period_end"] = _parse_date(m.group(2))

            # Quarterly from annual: if "perioodiks" match, assume 3 months
            if bp.get("quarterly_from_annual") and "perioodiks" in pattern:
                from dateutil.relativedelta import relativedelta
                from datetime import datetime, timedelta
                start = datetime.strptime(m.group(1), "%d.%m.%Y").date()
                result["billing_period_start"] = start.isoformat()
                result["billing_period_end"] = (
                    start + relativedelta(months=3) - timedelta(days=1)
                ).isoformat()
            break

    # Scan consecutive dates (e.g., Imatra Elekter)
    scan = bp.get("scan_consecutive_dates")
    if scan and "billing_period_start" not in result:
        lines = text.split("\n")
        start_line = scan.get("start_line", 0)
        end_line = scan.get("end_line", len(lines))
        for i in range(start_line, min(len(lines), end_line)):
            dm = re.match(r"^\s*(\d{2}\.\d{2}\.\d{4})\s*$", lines[i])
            if dm:
                result["billing_period_start"] = _parse_date(dm.group(1))
                if i + 1 < len(lines):
                    dm2 = re.match(r"^\s*(\d{2}\.\d{2}\.\d{4})\s*$", lines[i + 1])
                    if dm2:
                        result["billing_period_end"] = _parse_date(dm2.group(1))
                break

    return result


# ── Skip rules ───────────────────────────────────────────────────────────────

def should_skip(vendor_slug: str, pdf_path: str, text: str) -> bool:
    """Check if this bill should be skipped based on config skip_rules."""
    vc = get_vendors().get(vendor_slug, {})
    rules = vc.get("special", {}).get("skip_rules", [])
    filename = Path(pdf_path).name.lower()

    for rule in rules:
        # Filename match
        if "match_filename" in rule:
            if any(kw in filename for kw in rule["match_filename"]):
                return True

        # Text match (with optional "unless" override)
        if "match_text" in rule:
            if any(kw in text.lower() for kw in rule["match_text"]):
                # Check unless condition
                unless = rule.get("unless_text", [])
                if unless and any(re.search(pat, text) for pat in unless):
                    continue  # unless condition met, don't skip
                return True

    return False


# ── Special flags ────────────────────────────────────────────────────────────

def get_special(vendor_slug: str) -> dict:
    """Return the special config block for a vendor."""
    return get_vendors().get(vendor_slug, {}).get("special", {})


def has_pro_rate(vendor_slug: str) -> bool:
    return get_special(vendor_slug).get("pro_rate_across_period", False)


def get_heating_season(vendor_slug: str) -> Optional[dict]:
    return get_special(vendor_slug).get("spread_heating_season")


def get_month_alignment(vendor_slug: str) -> Optional[str]:
    return get_special(vendor_slug).get("month_alignment")


# ── Date helper ──────────────────────────────────────────────────────────────

def _parse_date(s: str) -> Optional[str]:
    from dateutil import parser as dp
    try:
        return dp.parse(s, dayfirst=True).date().isoformat()
    except Exception:
        return None
