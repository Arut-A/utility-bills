# API Reference

The `bills_api` service exposes a REST API on port `8888` (host) / `8000` (container).

---

## Authentication

Authenticated endpoints require the `X-API-Key` HTTP header:

```http
X-API-Key: your-api-key
```

Public endpoints (dashboard, PDFs, health) do not require authentication.

---

## Public Endpoints

### `GET /`

Returns the generated `dashboard.html` — the full interactive dashboard.

```
GET http://your-nas:8888/
```

---

### `GET /bills/{filename}`

Serves a raw PDF bill from `/data/raw/`.

```
GET http://your-nas:8888/bills/2024-10_electricity_31.51EUR.pdf
```

Filenames follow the canonical format: `YYYY-MM_vendor_category_amount_EUR.pdf`.

---

### `GET /health`

Health check endpoint. Returns `200 OK` when the service is running.

```json
{"status": "ok"}
```

---

## Authenticated Endpoints

All authenticated endpoints require `X-API-Key` header.

---

### `GET /api/bills`

List parsed bills with optional filters.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `vendor` | string | Filter by `vendor_category` (e.g. `electricity`) |
| `from_date` | string | Start date filter `YYYY-MM-DD` |
| `to_date` | string | End date filter `YYYY-MM-DD` |
| `page` | int | Page number (default `1`) |
| `per_page` | int | Results per page (default `50`, max `200`) |

**Example:**
```
GET /api/bills?vendor=electricity&from_date=2024-01-01&to_date=2024-12-31
```

**Response:**
```json
{
  "total": 12,
  "page": 1,
  "per_page": 50,
  "bills": [
    {
      "id": 42,
      "vendor_category": "electricity",
      "invoice_date": "2024-10-15",
      "billing_period_start": "2024-09-01",
      "billing_period_end": "2024-09-30",
      "total_amount": 31.51,
      "energy_kwh": 160.5,
      "raw_pdf_path": "/data/raw/2024-10_electricity_31.51EUR.pdf"
    }
  ]
}
```

---

### `GET /api/bills/monthly-totals`

Aggregated monthly totals per vendor category.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `year` | int | Filter by year |
| `vendor` | string | Filter by vendor |

**Response:**
```json
[
  {
    "month": "2024-10",
    "vendor_category": "electricity",
    "total": 31.51,
    "count": 1
  }
]
```

---

### `GET /api/bills/trends`

Year-over-year comparison for each vendor.

**Response:**
```json
[
  {
    "vendor_category": "electricity",
    "year": 2024,
    "total": 285.30,
    "monthly_avg": 23.78,
    "change_pct": -5.2
  }
]
```

---

### `GET /api/bills/per-unit-costs`

Unit cost analytics — cost per kWh, m³, or kg per month.

**Response:**
```json
[
  {
    "month": "2024-10",
    "electricity_eur_per_kwh": 0.1965,
    "gas_eur_per_m3": 0.5820,
    "water_eur_per_m3": 2.2300
  }
]
```

---

### `GET /api/meter-readings`

List manual meter readings.

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `vendor` | string | Filter by vendor |
| `from_date` | string | Start date |
| `to_date` | string | End date |

**Response:**
```json
[
  {
    "id": 1,
    "vendor_category": "electricity",
    "reading_date": "2024-10-31",
    "value": 12450.5,
    "unit": "kWh"
  }
]
```

---

### `POST /api/meter-readings`

Add a manual meter reading.

**Request body:**
```json
{
  "vendor_category": "electricity",
  "reading_date": "2024-10-31",
  "value": 12450.5,
  "unit": "kWh"
}
```

**Response:** `201 Created` with the created record.

---

### `POST /api/bills/manual`

Manually add a bill entry (for bills that cannot be auto-parsed).

**Request body:**
```json
{
  "vendor_category": "garbage",
  "invoice_date": "2024-10-01",
  "total_amount": 18.50,
  "billing_period_start": "2024-10-01",
  "billing_period_end": "2024-10-31",
  "notes": "Entered manually"
}
```

**Response:** `201 Created` with the created record.

---

## Error Responses

All endpoints return standard HTTP error codes:

| Code | Meaning |
|------|---------|
| `401 Unauthorized` | Missing or invalid `X-API-Key` |
| `404 Not Found` | Resource not found |
| `422 Unprocessable Entity` | Invalid request body or parameters |
| `500 Internal Server Error` | Unexpected server error |

Error response body:
```json
{
  "detail": "Description of what went wrong"
}
```
