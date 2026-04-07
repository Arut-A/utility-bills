"""
bill-parser/main.py
FastAPI service: accepts PDF paths, parses via regex, renames, saves to DB.
No per-bill Telegram — gmail-scraper sends a batch summary after the poll.
"""

import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from db import save_parsed_bill, save_parsing_error, is_already_parsed, rename_pdf_after_parse
from parser_engine import parse_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bill-parser")


def _check_api_key(request: Request) -> None:
    key = request.headers.get("X-API-Key", "")
    expected = os.environ.get("API_SECRET_KEY", "")
    if not expected or key != expected:
        raise HTTPException(status_code=403, detail="Invalid API key")


def _bills_raw_dir() -> Path:
    return Path(os.environ.get("BILLS_RAW_DIR", "/data/bills/raw")).resolve()


def _assert_safe_path(pdf_path: str) -> Path:
    try:
        resolved = Path(pdf_path).resolve()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid path")
    if not resolved.exists():
        raise HTTPException(status_code=422, detail=f"PDF not found: {pdf_path}")
    bills_dir = _bills_raw_dir()
    try:
        resolved.relative_to(bills_dir)
    except ValueError:
        if not str(resolved).lower().startswith(str(bills_dir).lower()):
            raise HTTPException(status_code=403, detail="pdf_path must be inside BILLS_RAW_DIR")
    return resolved


_VENDOR_DISPLAY = {
    "electricity_transport": "electricity_network",
    "gas_transport":         "gas_network",
}


def _canonical_pdf_name(result: dict) -> str:
    date_str = result.get("invoice_date") or ""
    year_month = date_str[:7] if len(date_str) >= 7 else "unknown"
    raw_category = result.get("vendor_category") or "unknown"
    vendor = re.sub(r"[^\w]", "_", _VENDOR_DISPLAY.get(raw_category, raw_category))
    amount = result.get("total_amount")
    currency = result.get("currency") or "EUR"
    amount_str = f"{amount:.2f}{currency}" if amount is not None else "unknown"
    return f"{year_month}_{vendor}_{amount_str}.pdf"


app = FastAPI(title="Bill Parser API", version="2.0.0")


class ParseRequest(BaseModel):
    pdf_path: str


class ParseResponse(BaseModel):
    id: str
    vendor_category: str | None
    invoice_number: str | None
    invoice_date: str | None
    total_amount: float | None
    currency: str | None
    status: str
    new_filename: str | None = None


@app.post("/parse", response_model=ParseResponse)
async def parse_bill(req: ParseRequest, request: Request):
    _check_api_key(request)
    pdf_path = req.pdf_path
    _assert_safe_path(pdf_path)

    if is_already_parsed(pdf_path):
        log.info("Already parsed: %s", pdf_path)
        raise HTTPException(status_code=409, detail="PDF already parsed.")

    try:
        result = parse_pdf(pdf_path)

        if result.get("status") == "skipped":
            return ParseResponse(
                id=result["id"], vendor_category=result.get("vendor_category"),
                invoice_number=None, invoice_date=None,
                total_amount=None, currency=None, status="skipped",
            )

        save_parsed_bill(result)

        new_filename = _canonical_pdf_name(result)
        new_path = str(Path(pdf_path).parent / new_filename)
        if new_path != pdf_path:
            try:
                rename_pdf_after_parse(result["id"], pdf_path, new_path)
                log.info("Renamed: %s -> %s", pdf_path, new_filename)
            except Exception as rename_exc:
                log.warning("Rename failed (non-fatal): %s", rename_exc)
                new_filename = Path(pdf_path).name

        return ParseResponse(
            id=result["id"],
            vendor_category=result.get("vendor_category"),
            invoice_number=result.get("invoice_number"),
            invoice_date=str(result.get("invoice_date") or ""),
            total_amount=result.get("total_amount"),
            currency=result.get("currency"),
            status=result["status"],
            new_filename=new_filename,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Parsing failed for %s: %s", pdf_path, exc)
        save_parsing_error(pdf_path, None, str(exc), None)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/generate-dashboard")
async def api_generate_dashboard(request: Request):
    """Regenerate dashboard from DB data. Called by gmail-scraper after poll."""
    _check_api_key(request)
    from dashboard_generator import generate_dashboard
    path = generate_dashboard()
    if path:
        return {"status": "ok", "path": path}
    raise HTTPException(status_code=500, detail="Dashboard generation failed")


@app.get("/health")
async def health():
    from datetime import datetime
    return {"status": "ok", "time": datetime.utcnow().isoformat()}
