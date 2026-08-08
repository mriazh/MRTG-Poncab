"""Integration tests for FastAPI web dashboard and reporting endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mrtg_poncab.auth import hash_password
from mrtg_poncab.config import settings
from mrtg_poncab.db import Database, TrafficSample
from mrtg_poncab.web.app import app


@pytest.fixture
def client_with_db(tmp_path: Path) -> TestClient:
    """Fixture providing a test client configured with a temporary database."""
    test_db_path = tmp_path / "web_test.db"
    db = Database(test_db_path)
    db.initialize()

    # Seed test user
    db.create_user("admin", hash_password("admin123"))

    # Seed some sample traffic records
    db.insert_traffic_sample(
        TrafficSample(
            timestamp="2026-09-15T08:00:00Z",
            rx_bytes=100_000_000,
            tx_bytes=50_000_000,
            rx_bps=10_000_000.0,
            tx_bps=5_000_000.0,
            epoch=1789459200,
            uptime="5d",
            status="UP",
        )
    )

    # Override app dependency
    settings.database_path = test_db_path
    client = TestClient(app, follow_redirects=False)
    return client


def test_login_page_renders(client_with_db: TestClient) -> None:
    """GET /login returns 200 with the HTML login form."""
    resp = client_with_db.get("/login")
    assert resp.status_code == 200
    assert "Authentication" in resp.text
    assert "username" in resp.text


def test_dashboard_unauthenticated_redirects(client_with_db: TestClient) -> None:
    """Unauthenticated browser request to / redirects to /login."""
    resp = client_with_db.get("/", headers={"Accept": "text/html"})
    assert resp.status_code in (302, 303, 307)
    assert "/login" in resp.headers.get("location", "")


def test_login_failure(client_with_db: TestClient) -> None:
    """POST /login with wrong password returns 401."""
    resp = client_with_db.post(
        "/login",
        data={"username": "admin", "password": "WrongPassword!"},
    )
    assert resp.status_code == 401
    assert "Invalid username or password" in resp.text


def test_login_success_and_session(client_with_db: TestClient) -> None:
    """POST /login with correct credentials sets session cookie and redirects."""
    resp = client_with_db.post(
        "/login",
        data={"username": "admin", "password": "admin123", "remember_me": "true"},
    )
    assert resp.status_code == 303
    assert "mrtg_session" in resp.cookies

    # Access protected dashboard using authenticated cookie
    dash_resp = client_with_db.get("/", cookies=resp.cookies)
    assert dash_resp.status_code == 200
    assert "MRTG-Poncab" in dash_resp.text
    assert "Traffic WAN" in dash_resp.text
    assert "theme-toggle" in dash_resp.text
    assert "Riwayat Sampel Trafik Terakhir" in dash_resp.text
    assert "countdown-timer" in dash_resp.text


def test_api_telemetry_endpoint(client_with_db: TestClient) -> None:
    """GET /api/telemetry returns structured JSON metrics and recent samples."""
    login_resp = client_with_db.post("/login", data={"username": "admin", "password": "admin123"})
    cookies = login_resp.cookies

    resp = client_with_db.get("/api/telemetry", cookies=cookies)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "UP"
    assert "current_in_formatted" in data
    assert "current_out_formatted" in data
    assert "recent_samples" in data
    assert isinstance(data["recent_samples"], list)
    assert len(data["recent_samples"]) > 0
    first_sample = data["recent_samples"][0]
    assert "timestamp_wib" in first_sample
    assert "inbound_formatted" in first_sample
    assert "outbound_formatted" in first_sample
    assert "status" in first_sample


def test_api_graph_png_endpoint(client_with_db: TestClient) -> None:
    """GET /api/graph.png returns binary image/png content."""
    login_resp = client_with_db.post("/login", data={"username": "admin", "password": "admin123"})
    cookies = login_resp.cookies

    resp = client_with_db.get("/api/graph.png?preset=today", cookies=cookies)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_api_exports_endpoints(client_with_db: TestClient) -> None:
    """GET /api/export/csv and /api/export/excel stream valid reports."""
    login_resp = client_with_db.post("/login", data={"username": "admin", "password": "admin123"})
    cookies = login_resp.cookies

    # CSV
    csv_resp = client_with_db.get("/api/export/csv?preset=today", cookies=cookies)
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert b"Inbound (Mbps)" in csv_resp.content

    # Excel
    xlsx_resp = client_with_db.get("/api/export/excel?preset=today", cookies=cookies)
    assert xlsx_resp.status_code == 200
    assert "openxmlformats" in xlsx_resp.headers["content-type"]
    assert len(xlsx_resp.content) > 1000


def test_logout_clears_cookie(client_with_db: TestClient) -> None:
    """GET /logout revokes session and deletes cookie."""
    login_resp = client_with_db.post("/login", data={"username": "admin", "password": "admin123"})
    cookies = login_resp.cookies

    logout_resp = client_with_db.get("/logout", cookies=cookies)
    assert logout_resp.status_code == 303
    assert "/login" in logout_resp.headers["location"]
