"""
bill-parser/parser_engine.py
Config-driven parsing: reads vendor definitions from /data/config/vendors.yaml.
  1. Extract text from PDF (PyMuPDF)
  2. Classify vendor (config-driven rules)
  3. Extract total, consumption, billing period via config-driven regex
  4. Extract invoice date, invoice number via generic regex
"""

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

import vendor_config

log = logging.getLogger("parser_engine")

# ── Generic regex helpers ────────────────────────────────────────────────────
DATE_RE = re.compile(r"\b(\d{2}[.\-/]\d{2}[.\-/]\d{4}|\d{4}[.\-/]\d{2}[.\-/]\d{2})\b")
AMOUNT_RE = re.compile(
    r"(?:total|kokku|summa|maksta)[^\d]{0,20}(\d+[.,]\d{2})(?!\d*[.\-/]\d{2,4}\b)",
    re.IGNORECASE,
)
INVOICE_RE = re.compile(
    r"(?:arve\s*nr|invoice\s*no\.?|number)[^\w]{0,10}([\w\-/]+)", re.IGNORECASE
)


def _parse_date(s: str) -> Optional[str]:
    from dateutil import parser as dp
    try:
        return dp.parse(s, dayfirst=True).date().isoformat()
    except Exception:
        return None


def _fallback_extract(text: str) -> dict:
    result = {}
    dates = DATE_RE.findall(text)
    if dates:
        result["invoice_date"] = _parse_date(dates[0])
    amount_m = AMOUNT_RE.search(text)
    if amount_m:
        result["total_incl_vat"] = float(amount_m.group(1).replace(",", "."))
    invoice_m = INVOICE_RE.search(text)
    if invoice_m:
        result["invoice_number"] = invoice_m.group(1).strip()
    return result


# ── Main entry point ─────────────────────────────────────────────────────────
def parse_pdf(pdf_path: str) -> dict:
    """Config-driven pipeline: extract text -> classify -> extract fields."""
    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    if len(text.strip()) < 50:
        log.warning("Very little text extracted from %s (scanned PDF?)", pdf_path)

    # Classify vendor from config
    vendor_category = vendor_config.classify_vendor(pdf_path, text)
    log.info("Classified as: %s", vendor_category)

    # Check skip rules
    if vendor_config.should_skip(vendor_category, pdf_path, text):
        log.info("Skipping per config rules: %s", pdf_path)
        return {
            "id": str(uuid.uuid4()),
            "vendor_category": vendor_category,
            "status": "skipped",
            "raw_pdf_path": pdf_path,
            "processed_at": datetime.utcnow().isoformat(),
        }

    # Total
    total = vendor_config.extract_total(vendor_category, text)
    status = "success"
    if total is None:
        fb = _fallback_extract(text)
        total = fb.get("total_incl_vat")
    if total is None:
        status = "partial"
        log.warning("Could not extract total from %s", pdf_path)

    # Invoice date
    invoice_date = None
    dates = DATE_RE.findall(text)
    if dates:
        invoice_date = _parse_date(dates[0])
    if not invoice_date:
        fname_date_m = re.match(r"(\d{4}-\d{2}-\d{2})", Path(pdf_path).name)
        if fname_date_m:
            invoice_date = fname_date_m.group(1)

    # Invoice number
    invoice_number = None
    inv_m = INVOICE_RE.search(text)
    if inv_m:
        invoice_number = inv_m.group(1).strip()

    # Consumption + billing period from config
    consumption = vendor_config.extract_consumption(vendor_category, text)

    return {
        "id": str(uuid.uuid4()),
        "vendor_category": vendor_category,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "billing_period_start": consumption.get("billing_period_start"),
        "billing_period_end": consumption.get("billing_period_end"),
        "total_amount": total,
        "currency": "EUR",
        "energy_kwh": consumption.get("energy_kwh"),
        "gas_m3": consumption.get("gas_m3"),
        "water_m3": consumption.get("water_m3"),
        "other_units": consumption.get("other_units"),
        "unit_type": consumption.get("unit_type"),
        "raw_pdf_path": pdf_path,
        "processed_at": datetime.utcnow().isoformat(),
        "status": status,
    }
