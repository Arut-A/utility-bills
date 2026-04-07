# Utility Bills App - Architecture

## Overview
Config-driven pipeline that scrapes utility bill emails from Gmail, parses PDF invoices via regex, stores structured data in MariaDB, and serves a web dashboard + Telegram notifications. Adding a new vendor requires only a YAML config change — no code modifications.

## System Diagram

```
Gmail (your-email@gmail.com)
  │
  ▼  (daily at 09:00)
┌──────────────────────┐     POST /parse      ┌──────────────────────┐
│  bills_gmail_scraper │ ──────────────────►   │   bills_parser       │
│  (Python + schedule) │                       │   (FastAPI :8001)    │
│                      │     POST /generate-   │                      │
│  - Gmail API poll    │ ──── dashboard ──────►│  - PDF regex parser  │
│  - PDF download      │                       │  - dashboard gen     │
│  - Telia body parse  │                       │  - DB writer         │
│  - Insurance label   │                       └───────────┬──────────┘
│  - Telegram notify   │                                   │
└──────────────────────┘                                   │
         │                                                 │
         │  Telegram Bot API                               │ SQLAlchemy
         ▼                                                 ▼
  ┌─────────────┐                                ┌──────────────────┐
  │  Telegram   │                                │  bills_mariadb   │
  │  @YourTelegramBot                                │  (MariaDB :3306) │
  │  chat:      │                                │  db: utility_bills│
  │  <TELEGRAM_CHAT_ID> │                                └────────┬─────────┘
  └─────────────┘                                         │
                                                          │
                   ┌──────────────────────┐               │
                   │  /data/config/       │               │
                   │  vendors.yaml        │◄──── shared volume
                   │  (vendor definitions)│
                   └──────────────────────┘
                              │
                    read by both containers
                              │
                              ▼
                    ┌──────────────────────┐
                    │   bills_api          │
                    │   (FastAPI :8000)    │
                    │   exposed :8888      │
                    │                      │
                    │  GET /               │──► dashboard.html
                    │  GET /bills/{file}   │──► PDF files
                    │  GET /api/bills      │──► JSON (API key)
                    │  POST /api/bills/manual│
                    │  GET /api/bills/monthly-totals│
                    │  GET /api/bills/trends│
                    │  GET /api/meter-readings│
                    │  POST /api/meter-readings│
                    └──────────────────────┘
                              │
                              ▼
                    https://your-nas.synology.me:8443
```

## Config-Driven Vendor System

### Adding a New Vendor
1. Edit `/data/config/vendors.yaml` (on NAS: `/volume1/bills/config/vendors.yaml`)
2. Add a YAML block under `vendors:`
3. Restart containers: `docker restart bills_parser bills_gmail_scraper`
4. No code changes needed

### Vendor YAML Schema
```yaml
  vendor_slug:
    display_name: "Human Name"
    provider: "Company Name"        # for Telegram summary
    dashboard:
      color: "#hex"                 # chart color
      label: "Dashboard Label"      # chip/legend text
    gmail:
      sender_domains: ["@domain.ee", "keyword"]
      label: "Gmail Label"          # optional: use Gmail label instead of sender search
    classification:
      filename_slugs: ["slug1"]     # match in original PDF filename
      text_keywords: ["keyword"]    # fallback: match in PDF text content
      require_keywords: ["kw"]      # text MUST contain one of these
      exclude_keywords: ["kw"]      # text must NOT contain these
    parsing:
      total_patterns:               # regex list, first match wins
        - 'KOKKU\s*([\d]+[.,][\d]{2})'
      consumption:                  # optional
        field: energy_kwh           # DB column: energy_kwh, gas_m3, water_m3, other_units
        unit: kWh
        patterns: ['([\d.,]+)\s*kWh']
      billing_period:               # optional
        patterns:
          - '(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})'
        scan_consecutive_dates:     # alternative: scan line ranges for dates
          start_line: 50
          end_line: 70
    special:                        # optional flags
      pro_rate_across_period: true  # split cost across billing period months
      spread_heating_season:        # spread cost across Sep-Apr
        start_month: 9
        end_month: 4
      month_alignment: billing_period_end  # align to service month
      no_pdf_variant: true          # extract from email body, not PDF
      skip_rules:                   # skip certain emails
        - match_filename: ["pakkumine"]
        - match_text: ["ettemaks"]
          unless_text: ['Netokaal:\s*[\d]+\s*kg']
```

### Current Vendors (11)
electricity, electricity_transport, gas, gas_transport, water, phone, internet, home_security, garbage, pellets, house_insurance

## Container Details

### bills_gmail_scraper
- **Source:** `/volume1/docker/utility-bills/utility-bills/gmail-scraper/`
- **Runtime:** Python 3.12, runs `main.py` with `schedule` library
- **Poll schedule:** Daily at 09:00 (env `POLL_DAILY_TIME=09:00`)
- **Gmail auth:** OAuth2 (installed app flow), scopes: `gmail.modify`
- **Token:** `/data/credentials/gmail_token.json` (on volume)
- **Google Cloud project:** `<GCP_PROJECT_ID>`
- **Key modules:** `main.py`, `vendor_config.py` (shared loader)
- **Flow:**
  1. Build Gmail query from vendors.yaml sender domains + subject keywords
  2. Download PDF attachments to `/data/raw/`
  3. POST each PDF to `bill-parser:8001/parse`
  4. Handle no-PDF variants (Telia internet: body extraction)
  5. Handle label-based search (insurance: Gmail label)
  6. Label emails `UTILITY_BILL`, mark as read, record in `raw_emails`
  7. Send batch Telegram summary + regenerated dashboard

### bills_parser
- **Source:** `/volume1/docker/utility-bills/utility-bills/bill-parser/`
- **Runtime:** Python 3.12, FastAPI on uvicorn :8001
- **Key modules:**
  - `vendor_config.py` — shared config loader, classification, extraction functions
  - `parser_engine.py` — PDF text extraction (MuPDF), delegates to vendor_config
  - `dashboard_generator.py` — generates HTML dashboard, reads colors/labels from config
  - `db.py` — SQLAlchemy DB operations
- **Endpoints:**
  - `POST /parse` — parse a PDF, save to DB, rename to canonical format
  - `POST /generate-dashboard` — regenerate `/data/dashboard.html`
- **Canonical filename:** `YYYY-MM_vendor_category_amount_EUR.pdf`

### bills_api
- **Source:** `/volume1/docker/utility-bills/utility-bills/api-server/`
- **Runtime:** Python 3.12, FastAPI on uvicorn :8000, exposed as :8888
- **Public endpoints:** `GET /` (dashboard), `GET /bills/{file}` (PDFs), `GET /health`
- **Authenticated endpoints** (X-API-Key header):
  - `GET /api/bills` — list with filters (vendor, date range, pagination)
  - `GET /api/bills/monthly-totals` — aggregated monthly view
  - `GET /api/bills/trends` — year-over-year comparison
  - `GET /api/bills/per-unit-costs` — unit cost analytics
  - `GET /api/meter-readings` / `POST /api/meter-readings`
  - `POST /api/bills/manual` — manual bill entry

### bills_mariadb
- **Database:** utility_bills
- **Tables:** parsed_bills, raw_emails, meter_readings, parsing_errors

## Dashboard
- Self-contained single HTML file with inline Chart.js
- Dark theme, responsive design
- **Charts:** stacked bar (monthly cost), multi-axis line (unit costs)
- **Filters:** year slicer chips, vendor category chips
- **Summary cards:** Latest Month, Monthly Avg, Total, Top Category — all filtered by year + vendor selection
- **Bill table:** clickable rows open PDFs, last 36 months
- Colors and labels injected from vendors.yaml at generation time

## Volume Layout (`/volume1/bills` -> `/data`)
```
/data/
  config/
    vendors.yaml              # master vendor config
  credentials/
    gmail_credentials.json    # OAuth client config
    gmail_token.json          # OAuth refresh token
  raw/                        # renamed PDFs
  dashboard.html              # generated dashboard
```

## Credentials
- **DB:** bills_user / <DB_PASSWORD> @ mariadb:3306 / utility_bills
- **API key:** <API_KEY> (X-API-Key header)
- **Gmail OAuth:** project `<GCP_PROJECT_ID>`, client `201562517181-...`
- **Telegram bot:** token `<TELEGRAM_BOT_TOKEN>`, chat `<TELEGRAM_CHAT_ID>`

## Networking
- All containers on `utility-bills_default` Docker bridge
- Internal: `mariadb`, `bill-parser` hostnames
- Only `bills_api` exposed (port 8888)
- Synology reverse proxy: https://your-nas.synology.me:8443 -> localhost:8888

## Operational Notes
- **Code changes:** Edit source in `/volume1/docker/utility-bills/utility-bills/<service>/`, then restart. For quick fixes: edit inside container + copy back using `docker run --rm -v /volume1/docker/utility-bills/utility-bills:/src alpine cp ...`
- **Module caching:** Parser's uvicorn keeps modules in memory. Restart after `.py` changes.
- **Config changes:** Edit vendors.yaml on the `/data` volume, restart parser + scraper.
- **Gmail token:** Refresh with `refresh_gmail_token.py` if expired. Publish app to "Production" in Google Cloud Console to prevent 7-day expiry.
- **Watchtower:** may recreate containers — keep source dir up to date.

## Data Span
- 23+ months: May 2024 to April 2026
- 11 vendor categories, 130+ parsed bills
- Bill PDFs at https://your-nas.synology.me:8443/bills/
