# Vendor Configuration Reference

All vendor-specific logic is defined in `config/vendors.yaml`. Both the Gmail scraper and the PDF parser load this file at startup. **No code changes are needed to add or modify a vendor.**

---

## File Structure

```yaml
gmail:
  subject_keywords:       # global Gmail subject filter (applied to all vendors)
    - arve
    - invoice

vendors:
  vendor_slug:            # internal key, used as DB vendor_category value
    display_name: "..."
    provider: "..."
    dashboard: { ... }
    gmail: { ... }
    classification: { ... }
    parsing: { ... }
    special: { ... }      # optional
```

---

## Top-Level Gmail Settings

```yaml
gmail:
  subject_keywords:
    - arve       # Estonian: "invoice"
    - invoice
    - bill
    - koguarve
    - e-arve
    - maksa      # "pay"
    - tasuda     # "to pay"
    - arvete
```

These keywords are combined with each vendor's `sender_domains` to build the Gmail search query. A message must match **at least one** subject keyword **and** come from a known sender domain to be considered.

---

## Per-Vendor Fields

### `display_name` and `provider`

```yaml
display_name: "Electricity"     # shown in dashboard labels
provider: "Alexela"             # shown in Telegram summary messages
```

---

### `dashboard`

Controls how this vendor appears on the dashboard.

```yaml
dashboard:
  color: "#f59e0b"      # hex color for chart bars and chips
  label: "Electricity"  # text shown on filter chips and chart legend
```

---

### `gmail`

Controls how the scraper finds emails for this vendor.

```yaml
gmail:
  sender_domains:
    - "@alexela.ee"    # match anywhere in the From address
    - "alexela"        # partial match also works
  label: "Electricity" # optional: search by Gmail label instead of sender
  gmail_label: "Electricity"  # label applied to processed emails in Gmail
```

- `sender_domains`: list of strings that must appear in the email's `From` field. Used to build the Gmail query `from:(domain1 OR domain2)`.
- `label`: if set, the scraper searches this Gmail label **instead of** using `sender_domains`. Useful when Gmail auto-filters emails into labels (e.g. House Insurance).
- `gmail_label`: the Gmail label applied to the email after successful processing. Allows tracking which emails have been handled.

---

### `classification`

Controls how the parser assigns a vendor to a parsed PDF. The parser tries each rule in order and uses the first match.

```yaml
classification:
  filename_slugs:
    - "alexela"           # string must appear in the original PDF filename (case-insensitive)
  text_keywords:
    - "alexela"           # string must appear in the extracted PDF text
    - "elektrienergia"
  require_keywords:
    - "some required term"  # at least one of these must appear in the PDF text
  exclude_keywords:
    - "maagaas"           # if any of these appear in the text, reject this vendor
    - "m³"
```

**Matching order:**
1. Check `filename_slugs` against the PDF filename.
2. If no filename match, check `text_keywords` against PDF full text.
3. Apply `require_keywords`: if set, at least one must be present in the text.
4. Apply `exclude_keywords`: if any are present, this vendor is rejected (allows Alexela gas and electricity to share a sender but be classified separately).

---

### `parsing`

Controls how amounts, consumption, and billing dates are extracted.

#### `total_patterns`

List of regex patterns tried in order. The first pattern with a match wins. The captured group must be the amount.

```yaml
parsing:
  total_patterns:
    - 'KOKKU EUR\s*([\d]+[.,][\d]{2})\b'
    - 'KOKKU:\s*\n\s*([\d]+[.,][\d]{2})'
```

- Use `[\d]+[.,][\d]{2}` to match amounts like `19,84` or `19.84`.
- Anchoring with `\b` prevents partial matches.

#### `consumption` (optional)

Extract a physical usage value alongside the monetary amount.

```yaml
parsing:
  consumption:
    field: energy_kwh          # DB column: energy_kwh | gas_m3 | water_m3 | other_units
    unit: kWh                  # display unit
    patterns:
      - 'Tarbimine:\s*([\d.,]+)\s*kWh'
    # For multi-line PDFs where reading appears twice (e.g. two meters):
    multi_value_pattern: '^([\d]+\.[\d]+)\s*kWh'
    multi_value_context: "Tarbimine"   # only scan lines near this word
    multi_value_limit: 2               # sum up to N values
    # Gas has a secondary field (kWh converted from m³):
    secondary:
      field: energy_kwh
      patterns:
        - 'Tarbimine:.*?m3,\s*([\d.,\s]+)\s*kWh'
```

Available `field` values:

| Field | Description |
|-------|-------------|
| `energy_kwh` | Electrical energy in kilowatt-hours |
| `gas_m3` | Gas volume in cubic metres |
| `water_m3` | Water volume in cubic metres |
| `other_units` | Generic units (e.g. pellets in kg) — use `unit_type` to specify |

#### `billing_period` (optional)

Extract the start and end dates of the billing period.

```yaml
parsing:
  billing_period:
    patterns:
      - '[Aa]rveldusperiood\s*[:\s]*(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})'
    # Alternative: scan a range of PDF lines for two consecutive dates
    scan_consecutive_dates:
      start_line: 50
      end_line: 70
```

- `patterns`: each must capture exactly **two** groups — `(start_date, end_date)` in `DD.MM.YYYY` format.
- `scan_consecutive_dates`: used when dates appear on separate lines without a consistent separator (e.g. Imatra Elekter network bills). Scans lines `start_line` to `end_line` and looks for two date-like strings close together.
- `quarterly_from_annual`: if `true`, an annual billing period is split into 4 equal quarters.

---

### `special` (optional)

Flags that change how the scraper or dashboard aggregator handles this vendor.

#### `pro_rate_across_period: true`

Splits a lump-sum invoice proportionally across all months in its billing period.

```yaml
special:
  pro_rate_across_period: true
```

Used for **House Insurance** — a single annual/quarterly invoice is divided day-by-day across the covered months so that the monthly chart shows a smooth cost rather than a spike.

#### `spread_heating_season`

Groups all pellet invoices within a heating season (Sep–Apr) and spreads the total evenly across those 8 months.

```yaml
special:
  spread_heating_season:
    start_month: 9    # September
    end_month: 4      # April
```

Used for **Pellets** — delivery invoices arrive irregularly (e.g. one big delivery in October), but the cost is spread to show monthly heating cost.

#### `month_alignment`

Controls which date is used as the "service month" for chart placement:

```yaml
special:
  month_alignment: billing_period_start   # or billing_period_end
```

| Value | Use case |
|-------|---------|
| `billing_period_start` | Bill covers the *previous* month; use start of period (Electricity Network, Gas Network) |
| `billing_period_end` | Meter reading spans two months; use end of period (Water) |

Without this flag, the invoice date itself is used as the cost month.

#### `no_pdf_variant: true`

The vendor sends invoices **without a PDF attachment** — the amount is embedded in the email body.

```yaml
special:
  no_pdf_variant: true
  no_pdf_query: 'from:@telia.ee ({subject_keywords}) -has:attachment -label:Archieve'
  body_total_patterns:
    - '[Mm]aksmisele kuuluv summa seisuga\s*(\d{2}\.\d{2}\.\d{4})\s*on\s*([\d]+[.,][\d]{2})'
    - 'Tasumisele kuulub[^\d]{0,30}([\d]+[.,][\d]{2})'
  body_date_pattern: '(\d{2}\.\d{2}\.\d{4})'
  body_invoice_pattern: 'arve\s*(?:nr\.?|number)?\s*([\d]+)'
```

Used for **Telia Internet** — the amount, date, and invoice number are extracted directly from the email HTML/text body. No PDF is downloaded. The record is stored with `raw_pdf_path = "email:<message_id>"`.

#### `skip_rules`

Skip emails that match certain conditions (avoids parsing offers, prepayments, etc.).

```yaml
special:
  skip_rules:
    # Skip if the original PDF filename contains any of these strings:
    - match_filename:
        - pakkumine       # "offer" in Estonian

    # Skip if the PDF text contains any of these strings,
    # UNLESS the text also contains one of the unless_text patterns:
    - match_text:
        - ettemaks        # "prepayment"
      unless_text:
        - 'Netokaal:\s*[\d., ]+\s*kg'   # skip only if no kg weight found (actual delivery)
```

Used for **Pellets** — Warmeston sends both order confirmations and delivery invoices. The skip rules filter out the confirmations while keeping the invoices that include actual weight data.

---

## Adding a New Vendor — Step by Step

1. **Find the sender domain** — check the `From` address on one of their emails.
2. **Get a sample PDF** — save it locally.
3. **Extract text** from the PDF (`pdftotext` or any PDF reader) and find the total amount line.
4. **Write a regex** for `total_patterns` that captures just the numeric amount.
5. **Identify billing dates** and write a `billing_period` pattern if needed.
6. **Add the YAML block** to `config/vendors.yaml`.
7. **Restart containers:**
   ```bash
   docker restart bills_parser bills_gmail_scraper
   ```
8. **Trigger a manual poll** and check the dashboard.

### Minimal example

```yaml
vendors:
  my_vendor:
    display_name: "My Vendor"
    provider: "Company Name"
    dashboard:
      color: "#6366f1"
      label: "My Vendor"
    gmail:
      sender_domains:
        - "@myvendor.ee"
      gmail_label: "My Vendor"
    classification:
      filename_slugs:
        - "myvendor"
      text_keywords:
        - "myvendor"
        - "my company as"
    parsing:
      total_patterns:
        - 'KOKKU\s*([\d]+[.,][\d]{2})'
```

### Full example with all optional fields

```yaml
vendors:
  my_vendor:
    display_name: "My Vendor"
    provider: "Company Name"
    dashboard:
      color: "#6366f1"
      label: "My Vendor"
    gmail:
      sender_domains:
        - "@myvendor.ee"
        - "myvendor"
      gmail_label: "My Vendor"
    classification:
      filename_slugs:
        - "myvendor"
      text_keywords:
        - "myvendor"
        - "company as"
      require_keywords:
        - "required term"
      exclude_keywords:
        - "exclude this"
    parsing:
      total_patterns:
        - 'KOKKU\s*([\d]+[.,][\d]{2})'
        - 'Total:\s*([\d]+[.,][\d]{2})'
      consumption:
        field: energy_kwh
        unit: kWh
        patterns:
          - 'Usage:\s*([\d.,]+)\s*kWh'
      billing_period:
        patterns:
          - '(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})'
    special:
      month_alignment: billing_period_start
      skip_rules:
        - match_filename:
            - offer
```

---

## Tips for Writing Regex Patterns

- **Amounts:** `([\d]+[.,][\d]{2})` matches `19,84` and `19.84`
- **Dates:** `(\d{2}\.\d{2}\.\d{4})` matches `31.12.2024`
- **Multi-line:** PDF text extraction joins lines with `\n`; use `\s*\n\s*` to match across line breaks
- **Test your regex** against the raw PDF text before adding to config:
  ```bash
  python3 -c "
  import fitz, re
  doc = fitz.open('invoice.pdf')
  text = '\n'.join(p.get_text() for p in doc)
  print(re.search(r'YOUR_PATTERN', text))
  "
  ```
- **Case sensitivity:** PDF text is case-sensitive; use `[Kk]okku` or `(?i)` flags if needed
