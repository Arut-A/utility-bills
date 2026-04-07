# Home Utility Bills Dashboard

A self-hosted, config-driven pipeline that automatically scrapes utility bill emails from Gmail, parses PDF invoices using regex, stores structured data in MariaDB, and serves a dark-themed web dashboard with charts and Telegram notifications.

**Adding a new utility vendor requires only a YAML config change — no code modifications.**

---

## Live Demo

**[⬇ Download demo/dashboard_demo.html](demo/dashboard_demo.html)** and open in any browser — no server needed. Uses realistic fake data across 24 months and 11 vendors.

> To regenerate: `python demo/generate_demo.py`

---

## Screenshots

**Overview — filters, summary cards, monthly cost by category**
![Overview](demo/screenshots/Screenshot%201.jpg)

**Unit costs (¢/kWh, €/m³), spending by category, year-over-year**
![Unit Costs](demo/screenshots/Screenshot%202.jpg)

**Month-over-month comparison + category breakdown cards**
![Month over Month](demo/screenshots/Screenshot%203.jpg)

**Consumption trends + 3-month budget forecast**
![Consumption & Forecast](demo/screenshots/Screenshot%204.jpg)

**Bill details table**
![Bill Table](demo/screenshots/Screenshot%205.jpg)

---

## Dashboard Features

### Summary Cards
Four KPI cards at the top — always reflect the active year/vendor filter:

| Card | Shows |
|------|-------|
| Latest Month | Total spend for the most recent month |
| Monthly Avg | Average over the last 12 visible months |
| Total | Grand total across all visible months |
| Top Category | Highest-spend vendor with total |

### Charts

**Monthly Cost (stacked bar)** — each bar is one month, stacked by vendor category. Scrollable for long histories. Emoji icons on each bar segment, total label above each bar.

**Unit Cost (multi-axis line)** — tracks price efficiency over time:
- Electricity: ¢/kWh (commodity + network combined)
- Gas: ¢/kWh and ¢/m³
- Water: €/m³
- Pellets: ¢/kWh (lifetime average)

**Spending by Category (doughnut)** — share of total spend per vendor, with percentage in tooltip.

**Year-over-Year table** — annual totals, monthly average, and % change vs prior year.

**Month-over-Month (stacked bar)** — same calendar months grouped side-by-side across years. Useful for spotting seasonal patterns.

**Consumption Trends (line)** — physical usage in kWh, m³ (gas), m³ (water) on dual axes.

**Budget Forecast** — 3-month projection using 6-month rolling average ±1σ band.

**Bill Table** — last 36 months, one row per bill, clickable to open the original PDF.

### Filters
Sticky filter bar at the top with chip toggles for **year**, **month**, and **vendor category**. All charts and cards update instantly. An "All/Clear" toggle is included for each group.

---

## Architecture Overview

```
Gmail Inbox
    │
    ▼  daily at 09:00
┌─────────────────────┐   POST /parse    ┌──────────────────────┐
│  bills_gmail_scraper│ ───────────────► │  bills_parser        │
│  (Python + schedule)│                  │  (FastAPI :8001)     │
│                     │  POST /generate- │                      │
│  • Gmail OAuth poll │ ─── dashboard ──►│  • PDF text extract  │
│  • PDF download     │                  │  • regex parsing     │
│  • email-body parse │                  │  • DB write          │
│  • Telegram notify  │                  │  • dashboard gen     │
└─────────────────────┘                  └──────────┬───────────┘
         │                                          │
         │ Telegram Bot API                         │ SQLAlchemy
         ▼                                          ▼
   ┌───────────┐                          ┌──────────────────┐
   │ Telegram  │                          │  bills_mariadb   │
   │  bot      │                          │  (MariaDB :3306) │
   └───────────┘                          └──────────────────┘
                                                    │
                      ┌─────────────────────────────┘
                      │
              ┌───────▼────────┐
              │  bills_api     │
              │ (FastAPI :8000)│
              │  exposed :8888 │
              │                │
              │ GET /          │──► dashboard.html
              │ GET /bills/... │──► PDF files
              │ GET /api/bills │──► JSON (auth required)
              └───────┬────────┘
                      │
              https://your-nas:8443
```

Four Docker containers communicate over a private bridge network (`utility-bills_default`). Only `bills_api` is exposed externally.

For the full architecture doc see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Repository Structure

```
.
├── README.md
├── .gitignore
├── dashboard_generator_patched.py   # Dashboard HTML generator (bill-parser service)
├── check_telia.py                   # Debug utility: inspect Gmail MIME structure
├── config/
│   ├── vendors.yaml                 # Master vendor config — the only file you edit to add vendors
│   ├── vendors/                     # Per-vendor YAML (split for readability, merged at runtime)
│   │   ├── electricity.yaml
│   │   ├── electricity_transport.yaml
│   │   ├── gas.yaml
│   │   ├── gas_transport.yaml
│   │   ├── water.yaml
│   │   ├── phone.yaml
│   │   ├── internet.yaml
│   │   ├── home_security.yaml
│   │   ├── garbage.yaml
│   │   ├── pellets.yaml
│   │   └── house_insurance.yaml
│   └── Utility_Bills_Architecture.md  # Detailed architecture reference
└── docs/
    ├── ARCHITECTURE.md     # System design, data flow, container details
    ├── SETUP.md            # Step-by-step deployment guide
    ├── VENDOR_CONFIG.md    # Complete guide to adding/configuring vendors
    └── API.md              # REST API reference
```

> **Note:** The source code for each Docker service (`main.py`, `parser_engine.py`, `vendor_config.py`, `db.py`) lives inside the Docker images and in `/volume1/docker/utility-bills/utility-bills/` on the NAS. This repository tracks configuration, the dashboard generator, and documentation.

---

## Current Vendors (11)

| Slug | Display Name | Provider | Special Handling |
|------|-------------|---------|-----------------|
| `electricity` | Electricity | Alexela | — |
| `electricity_transport` | Electricity Network | Imatra Elekter | Aligned to service month |
| `gas` | Gas | Alexela | — |
| `gas_transport` | Gas Network | Adven | Aligned to service month |
| `water` | Water | Viimsi Vesi | Aligned to billing period end |
| `phone` | Phone | Tele2 | — |
| `internet` | Internet | Telia | No-PDF: extracted from email body |
| `home_security` | Home Security | Telia | — |
| `garbage` | Garbage | Keskkonnateenused | — |
| `pellets` | Pellets | Warmeston | Cost spread across Sep–Apr heating season |
| `house_insurance` | House Insurance | Pro KM | Cost pro-rated across billing period |

---

## Quick Start

See [`docs/SETUP.md`](docs/SETUP.md) for the full deployment guide.

**Prerequisites:** Synology NAS (or any Docker host), Gmail account, Google Cloud project with Gmail API enabled, Telegram bot (optional).

```bash
# 1. Clone this repo onto your NAS
git clone https://github.com/youruser/utility-bills /volume1/docker/utility-bills

# 2. Copy and fill in your credentials
cp docker-compose.example.yml docker-compose.yml
# Edit DB password, API key, Telegram token, Gmail project ID

# 3. Place your Gmail OAuth credentials
cp gmail_credentials.json /volume1/bills/credentials/

# 4. Start
docker compose up -d
```

---

## Adding a New Vendor

Edit `config/vendors.yaml` — add one YAML block, restart two containers, done.

```yaml
vendors:
  my_new_vendor:
    display_name: "My Vendor"
    provider: "Company Name"
    dashboard:
      color: "#f59e0b"
      label: "My Vendor"
    gmail:
      sender_domains: ["@vendor.ee"]
    classification:
      filename_slugs: ["vendor"]
      text_keywords: ["vendor name"]
    parsing:
      total_patterns:
        - 'KOKKU\s*([\d]+[.,][\d]{2})'
```

```bash
docker restart bills_parser bills_gmail_scraper
```

See [`docs/VENDOR_CONFIG.md`](docs/VENDOR_CONFIG.md) for the full schema reference with all optional fields.

---

## Configuration

All runtime secrets are passed via environment variables in `docker-compose.yml`. No secrets are stored in this repository.

| Variable | Used by | Description |
|----------|---------|-------------|
| `DB_PASSWORD` | all | MariaDB password |
| `API_KEY` | api, parser | X-API-Key for authenticated endpoints |
| `TELEGRAM_BOT_TOKEN` | scraper | Telegram bot token |
| `TELEGRAM_CHAT_ID` | scraper | Telegram chat/group ID |
| `GMAIL_PROJECT_ID` | scraper | Google Cloud project ID |
| `POLL_DAILY_TIME` | scraper | Time to poll Gmail (default `09:00`) |

---

## License

Personal/self-hosted use. Not affiliated with any of the utility vendors mentioned in the config files.
