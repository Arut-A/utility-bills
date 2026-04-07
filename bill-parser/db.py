"""
bill-parser/db.py
Database access layer for the bill-parser service.
"""
import os
from datetime import datetime

from sqlalchemy import create_engine, text

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DB_URL")
        if not url:
            host = os.environ["DB_HOST"]
            port = os.environ.get("DB_PORT", "3306")
            name = os.environ["DB_NAME"]
            user = os.environ["DB_USER"]
            pwd  = os.environ["DB_PASSWORD"]
            url  = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset=utf8mb4"
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def is_already_parsed(pdf_path: str) -> bool:
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM parsed_bills WHERE raw_pdf_path = :p LIMIT 1"),
            {"p": pdf_path},
        ).fetchone()
    return row is not None


def save_parsed_bill(record: dict):
    with get_engine().begin() as conn:
        conn.execute(
            text("""
                INSERT INTO parsed_bills (
                    id, vendor_category, account_number, invoice_number,
                    billing_period_start, billing_period_end, invoice_date, due_date,
                    total_amount, total_excl_vat, vat_amount, currency,
                    energy_kwh, gas_m3, water_m3, internet_gb,
                    phone_minutes, other_units, unit_type, per_unit_cost,
                    details, raw_pdf_path, processed_at, status
                ) VALUES (
                    :id, :vendor_category, :account_number, :invoice_number,
                    :billing_period_start, :billing_period_end, :invoice_date, :due_date,
                    :total_amount, :total_excl_vat, :vat_amount, :currency,
                    :energy_kwh, :gas_m3, :water_m3, :internet_gb,
                    :phone_minutes, :other_units, :unit_type, :per_unit_cost,
                    :details, :raw_pdf_path, :processed_at, :status
                )
            """),
            {k: record.get(k) for k in [
                "id", "vendor_category", "account_number", "invoice_number",
                "billing_period_start", "billing_period_end", "invoice_date", "due_date",
                "total_amount", "total_excl_vat", "vat_amount", "currency",
                "energy_kwh", "gas_m3", "water_m3", "internet_gb",
                "phone_minutes", "other_units", "unit_type", "per_unit_cost",
                "details", "raw_pdf_path", "processed_at", "status",
            ]},
        )


def save_parsing_error(pdf_path: str, vendor_category, error_message: str, llm_raw):
    with get_engine().begin() as conn:
        conn.execute(
            text("""
                INSERT INTO parsing_errors
                    (pdf_path, vendor_category, error_message, llm_response_raw)
                VALUES (:pdf_path, :vendor_category, :error_message, :llm_raw)
            """),
            dict(pdf_path=pdf_path, vendor_category=vendor_category,
                 error_message=error_message, llm_raw=llm_raw),
        )

def rename_pdf_after_parse(record_id: str, old_path: str, new_path: str) -> None:
    """Move PDF to canonical name and update raw_pdf_path in both tables."""
    import shutil
    shutil.move(old_path, new_path)
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE parsed_bills SET raw_pdf_path = :p WHERE id = :id"),
            {"p": new_path, "id": record_id},
        )
        conn.execute(
            text("UPDATE raw_emails SET raw_pdf_path = :p WHERE raw_pdf_path = :old"),
            {"p": new_path, "old": old_path},
        )
