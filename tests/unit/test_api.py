"""
Tests for API server endpoints.
Uses FastAPI TestClient — no running containers needed.
Mocks DB calls since we're testing API logic, not DB.
"""
import importlib.util
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Set required env vars before importing the app
os.environ.setdefault("API_SECRET_KEY", "test-key-12345")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("DB_PASSWORD", "test_pass")


@pytest.fixture(scope="module")
def api_app():
    """Import the api-server main module (hyphen in dir name needs workaround)."""
    api_main_path = Path(__file__).parent.parent.parent / "api-server" / "main.py"
    spec = importlib.util.spec_from_file_location("api_main", str(api_main_path))
    module = importlib.util.module_from_spec(spec)
    with patch("sqlalchemy.create_engine", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(api_app):
    from fastapi.testclient import TestClient
    return TestClient(api_app.app)


class TestHealthEndpoint:
    """Health endpoint should always be accessible without auth."""

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestAuthMiddleware:
    """API endpoints should require valid API key."""

    def test_bills_without_key_returns_401_or_403(self, client):
        response = client.get("/api/bills")
        assert response.status_code in (401, 403)

    def test_bills_with_wrong_key_rejected(self, client):
        # 401 since the 2026-06-11 auth rework (wrong key = failed
        # authentication; 403 is reserved for valid-but-disallowed identities)
        response = client.get("/api/bills", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401


class TestDashboardEndpoint:
    """Dashboard serves HTML file."""

    def test_dashboard_404_when_missing(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PATH", str(tmp_path / "nonexistent.html"))
        response = client.get("/")
        assert response.status_code == 404

    def test_dashboard_serves_html(self, client, tmp_path, monkeypatch):
        html_file = tmp_path / "dashboard.html"
        html_file.write_text("<html><body>Test</body></html>")
        monkeypatch.setenv("DASHBOARD_PATH", str(html_file))
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


class TestBillPdfEndpoint:
    """PDF serving with path traversal protection."""

    def test_rejects_path_traversal(self, client):
        response = client.get("/bills/../../../etc/passwd")
        assert response.status_code in (400, 404)  # 404 if sanitized by framework

    def test_returns_404_for_missing_pdf(self, client, tmp_path, monkeypatch):
        monkeypatch.setenv("BILLS_RAW_DIR", str(tmp_path))
        response = client.get("/bills/nonexistent.pdf")
        assert response.status_code == 404
