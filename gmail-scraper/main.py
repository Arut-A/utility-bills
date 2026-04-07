"""
gmail-scraper/main.py
Polls Gmail for utility bill emails, downloads PDF attachments,
records metadata in DB, and triggers the bill-parser service.
Runs daily at POLL_DAILY_TIME (default 09:00) or on interval.
"""

import base64
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import schedule
import yaml
import vendor_config
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from sqlalchemy import create_engine, text

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("gmail-scraper")

# ── Config ───────────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

def load_config() -> dict:
    cfg_path = Path(__file__).parent / "config.yaml"
    with open(cfg_path) as f:
        return yaml.safe_load(f)

CFG = load_config()

# ── Database ─────────────────────────────────────────────────────────────────
def get_engine():
    url = os.environ.get("DB_URL")
    if not url:
        host = os.environ["DB_HOST"]
        port = os.environ.get("DB_PORT", "3306")
        name = os.environ["DB_NAME"]
        user = os.environ["DB_USER"]
        pwd  = os.environ["DB_PASSWORD"]
        url  = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset=utf8mb4"
    return create_engine(url, pool_pre_ping=True)

ENGINE = get_engine()

def email_already_processed(email_id: str) -> bool:
    with ENGINE.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM raw_emails WHERE email_id = :eid LIMIT 1"),
            {"eid": email_id},
        ).fetchone()
    return row is not None

def save_email_record(email_id, sender, subject, received_at, pdf_path):
    with ENGINE.begin() as conn:
        try:
            conn.execute(
                text("""
                    INSERT INTO raw_emails
                        (id, email_id, sender, subject, received_datetime, raw_pdf_path)
                    VALUES (:id, :eid, :sender, :subject, :received_at, :pdf_path)
                """),
                dict(id=str(uuid.uuid4()), eid=email_id, sender=sender,
                     subject=subject, received_at=received_at, pdf_path=pdf_path),
            )
        except Exception:
            pass  # duplicate — safe to ignore

# ── Bill-parser trigger ────────────────────────────────────────────────
def trigger_parse(pdf_path: str) -> dict | None:
    """POST the PDF path to bill-parser. Returns parsed result or None."""
    import httpx
    parser_url = os.environ.get("PARSER_SERVICE_URL", "http://bill-parser:8001")
    api_key    = os.environ.get("API_SECRET_KEY", "")
    try:
        resp = httpx.post(
            f"{parser_url}/parse",
            json={"pdf_path": pdf_path},
            headers={"X-API-Key": api_key},
            timeout=60.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            log.info("Parsed OK: %s -> vendor=%s amount=%s new=%s",
                     pdf_path, data.get("vendor_category"),
                     data.get("total_amount"), data.get("new_filename"))
            return data
        elif resp.status_code == 409:
            log.info("Already parsed, skipping: %s", pdf_path)
        else:
            log.warning("bill-parser returned %d for %s: %s",
                        resp.status_code, pdf_path, resp.text[:300])
    except Exception as exc:
        log.warning("Could not reach bill-parser for %s: %s", pdf_path, exc)
    return None


# ── Gmail helpers ─────────────────────────────────────────────────────────────
def get_gmail_service():
    creds = None
    token_path = os.environ.get("GMAIL_TOKEN_PATH", "/data/credentials/gmail_token.json")
    cred_path  = os.environ.get("GMAIL_CREDENTIALS_PATH", "/data/credentials/gmail_credentials.json")

    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(cred_path, SCOPES)
            creds = flow.run_local_server(port=0)
        Path(token_path).write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)

def sanitize_filename(s: str) -> str:
    s = re.sub(r"[^\w\-.]", "_", s)
    return s[:80]

def guess_vendor_slug(sender: str) -> str:
    domain = sender.split("@")[-1].split(".")[0] if "@" in sender else "unknown"
    return sanitize_filename(domain)

def ensure_label(service, label_name: str) -> str:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        if label["name"] == label_name:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow",
              "messageListVisibility": "show"},
    ).execute()
    log.info("Created Gmail label: %s", label_name)
    return created["id"]


def apply_vendor_label(service, msg_id: str, vendor_category: str):
    """After successful parse: add vendor-specific Gmail label + remove INBOX."""
    gmail_label_name = vendor_config.get_vendor_gmail_label(vendor_category)
    if not gmail_label_name:
        log.debug("No gmail_label configured for vendor %s, skipping", vendor_category)
        return
    try:
        label_id = ensure_label(service, gmail_label_name)
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]},
        ).execute()
        log.info("Applied label '%s' and removed INBOX for msg %s", gmail_label_name, msg_id)
    except Exception as exc:
        log.warning("Failed to apply vendor label for msg %s: %s", msg_id, exc)

def matches_filters(sender: str, subject: str) -> bool:
    cfg = CFG["gmail"]
    sender_match = any(
        allowed.lower() in sender.lower()
        for allowed in cfg["allowed_senders"]
    )
    subject_match = any(
        kw.lower() in subject.lower()
        for kw in cfg["subject_keywords"]
    )
    return sender_match and subject_match

def find_pdf_parts(payload):
    """Recursively find PDF attachment parts in a Gmail message payload."""
    parts = []
    if payload.get("parts"):
        for p in payload["parts"]:
            parts.extend(find_pdf_parts(p))
    if payload.get("filename") and payload["filename"].lower().endswith(".pdf"):
        if "reminder" not in payload["filename"].lower():
            parts.append(payload)
    return parts

# ── Core poll loop ────────────────────────────────────────────────────────────
def poll_gmail():
    log.info("Polling Gmail...")
    try:
        service = get_gmail_service()
        label_id = ensure_label(service, CFG["gmail"]["label_name"])
        bills_dir = Path(os.environ.get("BILLS_RAW_DIR", "/data/bills/raw"))
        bills_dir.mkdir(parents=True, exist_ok=True)

        cfg = CFG["gmail"]
        sender_q  = " OR ".join(f"from:{s}" for s in cfg["allowed_senders"])
        keyword_q = " OR ".join(f'subject:{k}' for k in cfg["subject_keywords"])
        q = f"has:attachment ({sender_q}) ({keyword_q}) -label:Archieve"
        log.info("Gmail query: %s", q)

        result = service.users().messages().list(
            userId="me", q=q, maxResults=200
        ).execute()
        messages = result.get("messages", [])
        log.info("Found %d matching messages (with PDF)", len(messages))

        new_count = 0
        new_bills = []  # collect parsed results for batch summary
        for msg_stub in messages:
            msg_id = msg_stub["id"]
            if email_already_processed(msg_id):
                continue

            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            sender  = headers.get("From", "")
            subject = headers.get("Subject", "")
            date_str = headers.get("Date", "")

            if not matches_filters(sender, subject):
                continue

            log.info("Processing: %s | %s", sender, subject)

            try:
                from email.utils import parsedate_to_datetime
                received_at = parsedate_to_datetime(date_str).astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                received_at = datetime.utcnow()

            date_prefix = received_at.strftime("%Y-%m-%d")
            vendor_slug = guess_vendor_slug(sender)
            subject_slug = sanitize_filename(subject)
            pdf_saved_path = None

            parts = msg["payload"].get("parts", [])
            for part in parts:
                mime = part.get("mimeType", "")
                if mime not in cfg["allowed_mime_types"]:
                    continue
                filename = part.get("filename", "")
                if not filename.lower().endswith(".pdf"):
                    continue

                attachment_id = part["body"].get("attachmentId")
                if not attachment_id:
                    continue

                attachment = service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=attachment_id
                ).execute()

                pdf_bytes = base64.urlsafe_b64decode(attachment["data"])
                out_name = f"{date_prefix}_{vendor_slug}_{subject_slug}.pdf"
                out_path = bills_dir / out_name
                counter = 1
                while out_path.exists():
                    out_path = bills_dir / f"{date_prefix}_{vendor_slug}_{subject_slug}_{counter}.pdf"
                    counter += 1

                out_path.write_bytes(pdf_bytes)
                pdf_saved_path = str(out_path)
                log.info("Saved PDF: %s", pdf_saved_path)

            # Apply label + mark read
            service.users().messages().modify(
                userId="me",
                id=msg_id,
                body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
            ).execute()

            save_email_record(msg_id, sender, subject, received_at, pdf_saved_path)

            # Trigger parsing
            if pdf_saved_path:
                parsed = trigger_parse(pdf_saved_path)
                if parsed:
                    new_bills.append(parsed)
                    apply_vendor_label(service, msg_id, parsed.get("vendor_category", ""))
                else:
                    # Parse failed — still remove from inbox
                    try:
                        service.users().messages().modify(
                            userId="me", id=msg_id,
                            body={"removeLabelIds": ["INBOX"]},
                        ).execute()
                    except Exception:
                        pass
                new_count += 1

            log.info("Recorded email %s in DB", msg_id)

        # ── Process no-PDF Telia internet bills (body-only) ─────────
        telia_results = _poll_telia_no_pdf(service, label_id)
        new_bills.extend(telia_results)
        new_count += len(telia_results)

        # ── Process insurance bills (Home insurance label) ────────────
        ins_results = _poll_insurance_bills(service, bills_dir, label_id)
        new_bills.extend(ins_results)

        log.info("Poll complete: %d new bill(s)", new_count + len(ins_results))

        # ── Send ONE summary + dashboard via Telegram ────────────────
        if new_bills:
            _send_telegram_summary(new_bills, bills_dir)

    except Exception as exc:
        log.exception("Error during Gmail poll: %s", exc)


def _get_email_body_text(payload: dict) -> str:
    """Recursively extract body from Gmail message payload. Prefers HTML
    (which often has more data than the plain-text summary)."""
    if payload.get("body", {}).get("data"):
        raw = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", " ", raw)

    # Collect both plain and HTML, prefer HTML (richer content)
    plain = ""
    html = ""
    for part in payload.get("parts", []):
        data = part.get("body", {}).get("data", "")
        if data and part.get("mimeType") == "text/html":
            raw = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            html = re.sub(r"<[^>]+>", " ", raw)
        elif data and part.get("mimeType") == "text/plain":
            plain = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if part.get("parts"):
            result = _get_email_body_text(part)
            if result:
                return result

    text = html or plain
    return re.sub(r"\s+", " ", text).strip() if text else ""


def _extract_telia_from_body(body_text: str, email_date_str: str) -> dict | None:
    """Extract invoice_date and total from Telia internet email body using regex.

    Telia body contains:
    - "Tasumisele kuulub" followed by an amount (the total)
    - Dates in DD.MM.YYYY format
    - Invoice number in the subject or body
    """
    # Pattern: "Maksmisele kuuluv summa seisuga DD.MM.YYYY on XX.XX €"
    total_m = re.search(
        r"[Mm]aksmisele kuuluv summa seisuga\s*(\d{2}\.\d{2}\.\d{4})\s*on\s*([\d]+[.,][\d]{2})",
        body_text,
    )
    if not total_m:
        # Fallback: "Tasumisele kuulub" pattern (older format)
        total_m2 = re.search(
            r"Tasumisele kuulub[^\d]{0,30}([\d]+[.,][\d]{2})",
            body_text, re.IGNORECASE,
        )
        if not total_m2:
            return None
        total = float(total_m2.group(1).replace(",", "."))
        # Date from email header
        invoice_date = None
    else:
        total = float(total_m.group(2).replace(",", "."))
        # Date is right in the pattern (seisuga DD.MM.YYYY)
        from dateutil import parser as dp
        try:
            invoice_date = dp.parse(total_m.group(1), dayfirst=True).date().isoformat()
        except Exception:
            invoice_date = None

    if not invoice_date:
        dates = re.findall(r"(\d{2}\.\d{2}\.\d{4})", body_text)
        if dates:
            from dateutil import parser as dp
            try:
                invoice_date = dp.parse(dates[0], dayfirst=True).date().isoformat()
            except Exception:
                pass
    if not invoice_date:
        try:
            from email.utils import parsedate_to_datetime
            invoice_date = parsedate_to_datetime(email_date_str).strftime("%Y-%m-%d")
        except Exception:
            pass

    # Invoice number
    inv_m = re.search(r"arve\s*(?:nr\.?|number)?\s*([\d]+)", body_text, re.IGNORECASE)
    invoice_number = inv_m.group(1) if inv_m else None

    return {
        "total_incl_vat": total,
        "invoice_date": invoice_date,
        "invoice_number": invoice_number,
    }


def _save_virtual_bill(vendor_category: str, invoice_date: str, total_amount: float,
                       invoice_number: str, source_desc: str):
    """Insert a parsed bill directly (no PDF — extracted from email body)."""
    with ENGINE.begin() as conn:
        try:
            conn.execute(text("""
                INSERT INTO parsed_bills
                    (id, vendor_category, invoice_number, invoice_date,
                     total_amount, currency, raw_pdf_path, processed_at, status)
                VALUES
                    (:id, :vendor_category, :invoice_number, :invoice_date,
                     :total_amount, 'EUR', :raw_pdf_path, :processed_at, 'success')
            """), {
                "id":              str(uuid.uuid4()),
                "vendor_category": vendor_category,
                "invoice_number":  invoice_number,
                "invoice_date":    invoice_date,
                "total_amount":    total_amount,
                "raw_pdf_path":    source_desc,
                "processed_at":    datetime.utcnow().isoformat(),
            })
            log.info("Saved virtual bill: vendor=%s date=%s amount=%.2f",
                     vendor_category, invoice_date, total_amount)
        except Exception:
            pass  # duplicate, ignore


def _poll_telia_no_pdf(service, label_id: str) -> list:
    """Poll Telia internet emails without PDF attachments, extract from body."""
    cfg = CFG["gmail"]
    keyword_q = " OR ".join(f'subject:{k}' for k in cfg["subject_keywords"])
    q = f"from:@telia.ee ({keyword_q}) -has:attachment -label:Archieve"
    log.info("Telia no-PDF query: %s", q)

    result = service.users().messages().list(
        userId="me", q=q, maxResults=50
    ).execute()
    messages = result.get("messages", [])
    if not messages:
        return []
    log.info("Found %d Telia no-PDF messages", len(messages))

    results = []
    for msg_stub in messages:
        msg_id = msg_stub["id"]
        if email_already_processed(msg_id):
            continue

        msg = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        date_str = headers.get("Date", "")

        try:
            from email.utils import parsedate_to_datetime
            received_at = parsedate_to_datetime(date_str).astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            received_at = datetime.utcnow()

        body_text = _get_email_body_text(msg["payload"])
        extracted = _extract_telia_from_body(body_text, date_str)

        if extracted and extracted.get("total_incl_vat") and extracted.get("invoice_date"):
            _save_virtual_bill(
                vendor_category="internet",
                invoice_date=extracted["invoice_date"],
                total_amount=float(extracted["total_incl_vat"]),
                invoice_number=extracted.get("invoice_number") or subject,
                source_desc=f"email:{msg_id}",
            )
            service.users().messages().modify(
                userId="me", id=msg_id,
                body={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD"]},
            ).execute()
            save_email_record(msg_id, sender, subject, received_at, None)
            apply_vendor_label(service, msg_id, "internet")
            log.info("Telia no-PDF bill: date=%s amount=%s",
                     extracted.get("invoice_date"), extracted.get("total_incl_vat"))
            results.append({"vendor_category": "internet",
                           "total_amount": float(extracted["total_incl_vat"]),
                           "invoice_date": extracted["invoice_date"]})
        else:
            log.warning("Could not extract from Telia no-PDF email %s", msg_id)

    return results


def _poll_insurance_bills(service, bills_dir: Path, utility_label_id: str) -> list:
    """Download PDFs from 'Home insurance' label and trigger parsing."""
    insurance_label = os.environ.get("INSURANCE_GMAIL_LABEL", "Home insurance")
    # Find label ID
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    label_id = None
    for l in labels:
        if l["name"] == insurance_label:
            label_id = l["id"]
            break
    if not label_id:
        log.debug("No '%s' Gmail label found, skipping insurance", insurance_label)
        return []

    results = []
    result = service.users().messages().list(
        userId="me", labelIds=[label_id], maxResults=20
    ).execute()
    messages = result.get("messages", [])
    if not messages:
        return []
    log.info("Found %d insurance messages", len(messages))

    for msg_stub in messages:
        msg_id = msg_stub["id"]
        if email_already_processed(msg_id):
            continue

        msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        sender = headers.get("From", "")
        subject = headers.get("Subject", "")
        date_str = headers.get("Date", "")

        try:
            from email.utils import parsedate_to_datetime
            received_at = parsedate_to_datetime(date_str).astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            received_at = datetime.utcnow()

        pdf_saved_path = None
        for part in find_pdf_parts(msg["payload"]):
            att_id = part["body"].get("attachmentId")
            if not att_id:
                continue
            att = service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=att_id
            ).execute()
            pdf_bytes = base64.urlsafe_b64decode(att["data"])
            fname = f"insurance_{part['filename']}"
            out_path = bills_dir / fname
            counter = 1
            while out_path.exists():
                out_path = bills_dir / f"insurance_{counter}_{part['filename']}"
                counter += 1
            out_path.write_bytes(pdf_bytes)
            pdf_saved_path = str(out_path)
            log.info("Saved insurance PDF: %s", pdf_saved_path)

        save_email_record(msg_id, sender, subject, received_at, pdf_saved_path)

        if pdf_saved_path:
            parsed = trigger_parse(pdf_saved_path)
            if parsed:
                results.append(parsed)
                apply_vendor_label(service, msg_id, parsed.get("vendor_category", ""))
            else:
                try:
                    service.users().messages().modify(
                        userId="me", id=msg_id,
                        body={"removeLabelIds": ["INBOX"]},
                    ).execute()
                except Exception:
                    pass

        log.info("Recorded insurance email %s", msg_id)

    return results


def _send_telegram_summary(new_bills: list, bills_dir: Path):
    """Send one batched Telegram summary + regenerated dashboard after poll."""
    import httpx
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not new_bills:
        return

    # Build summary with per-bill details
    total_files = len(list(bills_dir.glob("*.pdf")))
    lines = [f"\U0001f4ec Gmail poll: {len(new_bills)} new bill(s)\n"]

    PROVIDERS = vendor_config.get_provider_names()
    for b in new_bills:
        vendor = b.get("vendor_category", "?")
        provider = PROVIDERS.get(vendor, vendor)
        amount = b.get("total_amount")
        amt_str = f"\u20ac{amount:.2f}" if amount else "?"
        lines.append(f"  \U0001f4c4 {provider}: {amt_str}")

    lines.append(f"\n\U0001f4c1 {total_files} PDFs on disk")
    msg = "\n".join(lines)

    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10.0,
        )
    except Exception as exc:
        log.warning("Telegram summary failed: %s", exc)

    # Regenerate dashboard via bill-parser and send
    parser_url = os.environ.get("PARSER_SERVICE_URL", "http://bill-parser:8001")
    api_key = os.environ.get("API_SECRET_KEY", "")
    dashboard_path = os.environ.get("DASHBOARD_PATH", "/data/dashboard.html")
    try:
        httpx.post(
            f"{parser_url}/generate-dashboard",
            headers={"X-API-Key": api_key},
            timeout=30.0,
        )
        if os.path.exists(dashboard_path):
            with open(dashboard_path, "rb") as f:
                httpx.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": chat_id, "caption": "\U0001f4ca Dashboard"},
                    files={"document": ("dashboard.html", f, "text/html")},
                    timeout=30.0,
                )
            log.info("Dashboard sent to Telegram")
    except Exception as exc:
        log.warning("Dashboard send failed: %s", exc)


def main():
    poll_time = os.environ.get("POLL_DAILY_TIME")
    if poll_time:
        log.info("Gmail scraper starting (daily at %s)", poll_time)
        schedule.every().day.at(poll_time).do(poll_gmail)
    else:
        interval = int(os.environ.get("POLL_INTERVAL_SECONDS",
                                      CFG["scraper"]["poll_interval_seconds"]))
        log.info("Gmail scraper starting (every %ds)", interval)
        schedule.every(interval).seconds.do(poll_gmail)

    poll_gmail()  # Run immediately on start
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
