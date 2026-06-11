"""
Integration tests — test the full bills pipeline across container boundaries.
These tests call the actual running containers via HTTP.
Run only when containers are up: pytest tests/integration/ -v

Uses the live MariaDB but doesn't modify production data.
"""
import os
import pytest
import httpx

API_URL = os.environ.get("BILLS_API_URL", "http://192.168.86.41:8888")
PARSER_URL = os.environ.get("PARSER_URL", "http://192.168.86.41:8001")
API_KEY = os.environ.get("API_SECRET_KEY", "T9_QDK5wo5-P33fq3rhc6M0guogLPdkJ")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "rQTlz8FxpfAxRhozBIB8GKfbSdW-SZGf")


class TestAPIHealth:
    """API server is reachable and healthy."""

    def test_health_endpoint(self):
        resp = httpx.get(f"{API_URL}/health", timeout=10)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_dashboard_requires_auth(self):
        resp = httpx.get(f"{API_URL}/", timeout=10, follow_redirects=True)
        assert resp.status_code == 401

    def test_dashboard_with_token(self):
        resp = httpx.get(f"{API_URL}/?token={DASHBOARD_TOKEN}", timeout=10)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_pdf_path_traversal_blocked(self):
        resp = httpx.get(f"{API_URL}/bills/../../../etc/passwd", timeout=10)
        assert resp.status_code in (400, 404)


class TestAPIData:
    """API returns correct bill data."""

    def test_bills_list_requires_api_key(self):
        resp = httpx.get(f"{API_URL}/api/bills", timeout=10)
        assert resp.status_code in (401, 403)

    def test_bills_list_with_key(self):
        resp = httpx.get(f"{API_URL}/api/bills",
                         headers={"X-API-Key": API_KEY}, timeout=10)
        assert resp.status_code == 200
        bills = resp.json()
        assert isinstance(bills, list)
        assert len(bills) > 0

    def test_monthly_totals(self):
        resp = httpx.get(f"{API_URL}/api/bills/monthly-totals",
                         headers={"X-API-Key": API_KEY}, timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        assert "vendor_category" in data[0]
        assert "total_amount" in data[0]

    def test_trends(self):
        resp = httpx.get(f"{API_URL}/api/bills/trends",
                         headers={"X-API-Key": API_KEY}, timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_bill_has_required_fields(self):
        resp = httpx.get(f"{API_URL}/api/bills?limit=1",
                         headers={"X-API-Key": API_KEY}, timeout=10)
        bill = resp.json()[0]
        assert "vendor_category" in bill
        assert "total_amount" in bill
        assert "invoice_date" in bill


class TestParserHealth:
    """Parser service is reachable (internal, tested via API container)."""

    def test_parser_health_via_docker(self):
        """Parser runs on internal network — test via docker exec."""
        import subprocess
        result = subprocess.run(
            ["ssh", "nas", "/usr/local/bin/docker exec bills_api curl -sf http://bill-parser:8001/health"],
            capture_output=True, text=True, timeout=15
        )
        assert '"ok"' in result.stdout
