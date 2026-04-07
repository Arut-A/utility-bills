"""
bill-parser/telegram_notify.py
Send notifications via Telegram Bot API.
"""
import logging
import os

import httpx

log = logging.getLogger("telegram_notify")


def send_telegram(message: str) -> None:
    """Send a message via Telegram Bot API. No-op if env vars missing."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.debug("Telegram not configured, skipping")
        return
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            log.warning("Telegram API returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)


_VENDOR_PROVIDERS = {
    "electricity":           "Alexela",
    "electricity_transport": "Imatra Elekter",
    "gas":                   "Alexela",
    "gas_transport":         "Adven",
    "phone":                 "Tele2",
    "internet":              "Telia",
    "home_security":         "Telia",
    "garbage":               "Keskkonnateenused",
    "pellets":               "Warmeston",
    "water":                 "Viimsi Vesi",
    "house_insurance":       "Pro Kindlustusmaakler",
}

_VENDOR_DISPLAY = {
    "electricity_transport": "Electricity Network",
    "gas_transport":         "Gas Network",
    "electricity":           "Electricity",
    "gas":                   "Gas",
    "phone":                 "Phone",
    "internet":              "Internet",
    "home_security":         "Home Security",
    "garbage":               "Garbage",
    "pellets":               "Pellets",
    "water":                 "Water",
    "house_insurance":       "House Insurance",
}


def notify_bill_parsed(result: dict, new_filename: str) -> None:
    """Send a formatted Telegram notification for a parsed bill."""
    vendor_cat = result.get("vendor_category") or "unknown"
    provider = _VENDOR_PROVIDERS.get(vendor_cat, vendor_cat)
    display = _VENDOR_DISPLAY.get(vendor_cat, vendor_cat)
    total = result.get("total_amount")
    currency = result.get("currency") or "EUR"
    inv_date = result.get("invoice_date") or "\u2014"
    amount_str = f"{total:.2f} {currency}" if total is not None else "unknown"

    msg = (
        f"\u2705 New bill parsed:\n"
        f"\U0001f4c4 {new_filename}\n"
        f"\U0001f3e2 Provider: {provider}\n"
        f"\u26a1 Category: {display}\n"
        f"\U0001f4b0 Total: \u20ac{amount_str}\n"
        f"\U0001f4c5 Date: {inv_date}"
    )
    send_telegram(msg)


def notify_gmail_poll(new_count: int, total_count: int) -> None:
    """Send a summary after Gmail poll completes."""
    if new_count == 0:
        return
    msg = (
        f"\U0001f4ec Gmail poll complete:\n"
        f"\U0001f4e5 {new_count} new bill(s) downloaded\n"
        f"\U0001f4c1 {total_count} total in inbox"
    )
    send_telegram(msg)


def send_telegram_document(file_path: str, caption: str = "") -> None:
    """Send a file as a Telegram document."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        with open(file_path, "rb") as f:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (os.path.basename(file_path), f, "text/html")},
                timeout=30.0,
            )
        if resp.status_code != 200:
            log.warning("Telegram document send returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Telegram document send failed: %s", exc)
