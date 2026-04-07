"""
api-server/main.py
FastAPI REST API consumed by the Android app.
Secured with API key header.
"""

import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Security
from fastapi.responses import FileResponse
from fastapi.security.api_key import APIKeyHeader
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("api-server")

app = FastAPI(title="Utility Bills API", version="1.0.0")

# ── Auth ───────────────────────────────────────────────────────────────────────
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=True)

def require_api_key(key: str = Security(API_KEY_HEADER)):
    if key != os.environ.get("API_SECRET_KEY", ""):
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key

# ── DB ─────────────────────────────────────────────────────────────────────────
_engine = None

def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DB_URL")
        if url:
            _engine = create_engine(url, pool_pre_ping=True)
            return _engine
        host = os.environ["DB_HOST"]
        port = os.environ.get("DB_PORT", "3306")
        name = os.environ["DB_NAME"]
        user = os.environ["DB_USER"]
        pwd  = os.environ["DB_PASSWORD"]
        url  = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset=utf8mb4"
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine

def rows_to_dicts(result):
    cols = result.keys()
    return [dict(zip(cols, row)) for row in result.fetchall()]

# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/")
async def dashboard():
    """Serve the dashboard HTML."""
    path = os.environ.get("DASHBOARD_PATH", "/data/dashboard.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(path, media_type="text/html")


@app.get("/bills/{filename}")
async def serve_bill_pdf(filename: str):
    """Serve a PDF bill file."""
    bills_dir = os.environ.get("BILLS_RAW_DIR", "/data/raw")
    # Sanitize: only allow filenames, no path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(bills_dir, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/bills", dependencies=[Depends(require_api_key)])
async def list_bills(
    vendor: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),   # YYYY-MM-DD
    date_to: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    """List parsed bills with optional filters."""
    conditions = ["status = 'success'"]
    params = {"limit": limit, "offset": offset}
    if vendor:
        conditions.append("vendor_category = :vendor")
        params["vendor"] = vendor
    if date_from:
        conditions.append("invoice_date >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("invoice_date <= :date_to")
        params["date_to"] = date_to

    where = " AND ".join(conditions)
    sql = f"""
        SELECT id, vendor_category, invoice_number, invoice_date, due_date,
               billing_period_start, billing_period_end,
               total_amount, currency, energy_kwh, gas_m3, water_m3,
               internet_gb, phone_minutes, other_units, unit_type, per_unit_cost
        FROM parsed_bills
        WHERE {where}
        ORDER BY invoice_date DESC
        LIMIT :limit OFFSET :offset
    """
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return rows_to_dicts(result)


@app.get("/api/bills/monthly-totals", dependencies=[Depends(require_api_key)])
async def monthly_totals(
    vendor: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
):
    """Monthly totals per vendor."""
    conditions = []
    params = {}
    if vendor:
        conditions.append("vendor_category = :vendor")
        params["vendor"] = vendor
    if year:
        conditions.append("year = :year")
        params["year"] = year
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM v_monthly_totals {where} ORDER BY year DESC, month DESC"
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return rows_to_dicts(result)


@app.get("/api/bills/trends", dependencies=[Depends(require_api_key)])
async def trends(vendor: Optional[str] = Query(None)):
    """YoY cost comparison per vendor/month."""
    params = {}
    where = ""
    if vendor:
        where = "WHERE vendor_category = :vendor"
        params["vendor"] = vendor
    sql = f"SELECT * FROM v_yoy_comparison {where} ORDER BY current_year DESC, month DESC"
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return rows_to_dicts(result)


@app.get("/api/bills/per-unit-costs", dependencies=[Depends(require_api_key)])
async def per_unit_costs(vendor: Optional[str] = Query(None)):
    """Average per-unit cost over time, grouped by vendor and month."""
    params = {}
    vendor_filter = "AND vendor_category = :vendor" if vendor else ""
    if vendor:
        params["vendor"] = vendor
    sql = f"""
        SELECT
            vendor_category,
            unit_type,
            YEAR(invoice_date)  AS year,
            MONTH(invoice_date) AS month,
            AVG(per_unit_cost)  AS avg_per_unit_cost,
            MIN(per_unit_cost)  AS min_per_unit_cost,
            MAX(per_unit_cost)  AS max_per_unit_cost,
            COUNT(*)            AS sample_count
        FROM parsed_bills
        WHERE status = 'success'
          AND per_unit_cost IS NOT NULL
          {vendor_filter}
        GROUP BY vendor_category, unit_type, YEAR(invoice_date), MONTH(invoice_date)
        ORDER BY year DESC, month DESC
    """
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return rows_to_dicts(result)


@app.get("/api/meter-readings", dependencies=[Depends(require_api_key)])
async def list_meter_readings(
    meter_id: Optional[str] = Query(None),
    vendor: Optional[str] = Query(None),
    limit: int = Query(50),
):
    params = {"limit": limit}
    conditions = []
    if meter_id:
        conditions.append("meter_id = :meter_id")
        params["meter_id"] = meter_id
    if vendor:
        conditions.append("vendor_category = :vendor")
        params["vendor"] = vendor
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM meter_readings {where} ORDER BY reading_date DESC LIMIT :limit"
    with get_engine().connect() as conn:
        result = conn.execute(text(sql), params)
        return rows_to_dicts(result)


@app.post("/api/meter-readings", dependencies=[Depends(require_api_key)])
async def add_meter_reading(body: dict):
    """Manually add a meter reading (from Android app)."""
    required = {"meter_id", "vendor_category", "reading_date", "reading_value", "unit_type"}
    if not required.issubset(body.keys()):
        raise HTTPException(status_code=400, detail=f"Missing fields: {required - body.keys()}")
    with get_engine().begin() as conn:
        conn.execute(
            text("""
                INSERT INTO meter_readings
                    (meter_id, vendor_category, reading_date, reading_value, unit_type, notes)
                VALUES (:meter_id, :vendor_category, :reading_date, :reading_value, :unit_type, :notes)
            """),
            {**body, "notes": body.get("notes")},
        )
    return {"status": "created"}


@app.post("/api/bills/manual", dependencies=[Depends(require_api_key)])
async def add_manual_bill(body: dict):
    """Manually enter a bill from the Android app."""
    import uuid
    from datetime import datetime
    record = {
        "id": str(uuid.uuid4()),
        "vendor_category":      body.get("vendor_category"),
        "account_number":       body.get("account_number"),
        "invoice_number":       body.get("invoice_number"),
        "billing_period_start": body.get("billing_period_start"),
        "billing_period_end":   body.get("billing_period_end"),
        "invoice_date":         body.get("invoice_date"),
        "due_date":             body.get("due_date"),
        "total_amount":         body.get("total_amount"),
        "currency":             body.get("currency", "EUR"),
        "energy_kwh":           body.get("energy_kwh"),
        "gas_m3":               body.get("gas_m3"),
        "water_m3":             body.get("water_m3"),
        "internet_gb":          body.get("internet_gb"),
        "phone_minutes":        body.get("phone_minutes"),
        "other_units":          body.get("other_units"),
        "unit_type":            body.get("unit_type"),
        "per_unit_cost":        None,
        "raw_pdf_path":         "manual_entry",
        "processed_at":         datetime.utcnow().isoformat(),
        "status":               "success",
    }
    # Compute per-unit cost
    amount = record["total_amount"]
    usage  = (record["energy_kwh"] or record["gas_m3"] or
              record["water_m3"] or record["other_units"])
    if amount and usage:
        record["per_unit_cost"] = round(float(amount) / float(usage), 6)

    with get_engine().begin() as conn:
        conn.execute(
            text("""
                INSERT INTO parsed_bills (
                    id, vendor_category, account_number, invoice_number,
                    billing_period_start, billing_period_end, invoice_date, due_date,
                    total_amount, currency, energy_kwh, gas_m3, water_m3,
                    internet_gb, phone_minutes, other_units, unit_type, per_unit_cost,
                    raw_pdf_path, processed_at, status
                ) VALUES (
                    :id, :vendor_category, :account_number, :invoice_number,
                    :billing_period_start, :billing_period_end, :invoice_date, :due_date,
                    :total_amount, :currency, :energy_kwh, :gas_m3, :water_m3,
                    :internet_gb, :phone_minutes, :other_units, :unit_type, :per_unit_cost,
                    :raw_pdf_path, :processed_at, :status
                )
            """),
            record,
        )
    return {"status": "created", "id": record["id"]}
