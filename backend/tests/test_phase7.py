"""Phase 7 - production hardening: CORS from env, error handling without stack traces, upload validation, health, static UI."""
import importlib
import io
import os
import sys

import pytest
from fastapi.testclient import TestClient

import main
import data_source

client = TestClient(main.app, raise_server_exceptions=False)
GOOD_CSV = ("supplier_gstin,supplier_name,invoice_no,invoice_date,taxable_value,cgst,sgst,igst\n"
            "27AAACS1234A1Z5,Test Supplier,INV-1,2026-07-01,10000,900,900,0\n")


@pytest.fixture(autouse=True)
def _restore():
    yield
    client.post("/api/reset?dataset=sample")


def _files(purchase: bytes, portal: bytes):
    return {"purchase_file": ("p.csv", io.BytesIO(purchase), "text/csv"), "portal_file": ("g.csv", io.BytesIO(portal), "text/csv")}


# ---------------------------------------------------------------------------------------------- health
def test_health_has_version_datasets_and_ai_status_without_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["version"] == main.VERSION and h["datasets"] == ["sample", "realistic"]
    assert h["ai"]["configured"] is True and "sk-secret" not in str(h)


# ---------------------------------------------------------------------------------------------- CORS
def test_cors_origins_parsing(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert main._cors_origins() == ["*"]
    monkeypatch.setenv("CORS_ORIGINS", " https://itc-shield.vercel.app/, https://app.example.com ,")
    assert main._cors_origins() == ["https://itc-shield.vercel.app", "https://app.example.com"]


def test_cors_headers_are_sent_for_configured_origin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://ui.example.com")
    m = importlib.reload(main)                     # middleware is configured at import time
    try:
        c = TestClient(m.app)
        ok = c.get("/api/health", headers={"Origin": "https://ui.example.com"})
        assert ok.headers.get("access-control-allow-origin") == "https://ui.example.com"
        other = c.get("/api/health", headers={"Origin": "https://evil.example.com"})
        assert other.headers.get("access-control-allow-origin") is None
    finally:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        importlib.reload(main)


# ---------------------------------------------------------------------------------------------- error handling
def test_unhandled_errors_return_json_without_stack_trace(monkeypatch):
    def explode():
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(main, "_dashboard_payload", explode)
    r = client.get("/api/dashboard")
    assert r.status_code == 500
    assert r.json() == {"detail": "Something went wrong on the server. Please try again."}
    assert "Traceback" not in r.text and "secret internal detail" not in r.text


def test_results_before_analysis_give_a_clear_409():
    client.post("/api/reset?dataset=sample")
    for path in ("/api/dashboard", "/api/exposure", "/api/priority", "/api/suppliers", "/api/invoices"):
        r = client.get(path)
        assert r.status_code == 409 and "Run the analysis first" in r.json()["detail"], path


def test_unknown_api_route_is_json_404():
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.json()["detail"]


# ---------------------------------------------------------------------------------------------- upload validation
def test_upload_empty_file_gives_useful_error():
    r = client.post("/api/upload", files=_files(b"", GOOD_CSV.encode()))
    assert r.status_code == 422 and "purchase file is empty" in r.json()["detail"]
    r = client.post("/api/upload", files=_files(GOOD_CSV.encode(), b"   \n"))
    assert r.status_code == 422 and "portal file is empty" in r.json()["detail"]


def test_upload_header_only_and_missing_columns():
    header_only = GOOD_CSV.splitlines()[0] + "\n"
    r = client.post("/api/upload", files=_files(header_only.encode(), GOOD_CSV.encode()))
    assert r.status_code == 422 and "no invoices" in r.json()["detail"]
    r = client.post("/api/upload", files=_files(b"a,b\n1,2\n", GOOD_CSV.encode()))
    assert r.status_code == 422 and "missing columns" in r.json()["detail"]


def test_upload_non_csv_content_gives_useful_error():
    r = client.post("/api/upload", files=_files(b"\xff\xfe\x00binary\x00junk", GOOD_CSV.encode()))
    assert r.status_code == 422
    d = r.json()["detail"]
    assert "CSV" in d or "UTF-8" in d
    assert "Traceback" not in r.text
    r = client.post("/api/upload", files=_files(b"just one line of prose with no commas\n", GOOD_CSV.encode()))
    assert r.status_code == 422 and ("does not look like a CSV" in r.json()["detail"] or "missing columns" in r.json()["detail"])


def test_upload_too_large_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    r = client.post("/api/upload", files=_files((GOOD_CSV * 20).encode(), GOOD_CSV.encode()))
    assert r.status_code == 413 and "larger than" in r.json()["detail"]


def test_upload_valid_files_still_work_and_blank_tax_cells_are_allowed():
    purchase = GOOD_CSV + "27AAACS1234A1Z5,Test Supplier,INV-2,2026-07-02,5000,,,900\n"   # blank CGST/SGST on an IGST invoice
    r = client.post("/api/upload", files=_files(purchase.encode(), GOOD_CSV.encode()))
    assert r.status_code == 200 and r.json()["purchase_rows"] == 2
    assert client.post("/api/analyze").status_code == 200
    d = client.get("/api/dashboard").json()
    assert d["source"] == "upload" and d["summary"]["issue_count"] == 1          # INV-2 is not on the portal
    assert client.get("/api/health").json()["source"] == "upload"


# ---------------------------------------------------------------------------------------------- static UI (single-service mode)
def test_static_ui_is_served_when_a_build_exists(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><body><div id=root></div></body></html>")
    (dist / "assets" / "app.js").write_text("console.log('ok')")
    monkeypatch.setenv("STATIC_DIR", str(dist))
    m = importlib.reload(main)
    try:
        c = TestClient(m.app, raise_server_exceptions=False)
        assert c.get("/").status_code == 200 and "id=root" in c.get("/").text
        assert c.get("/suppliers/anything").text == c.get("/").text               # SPA fallback
        assert c.get("/assets/app.js").text == "console.log('ok')"
        assert c.get("/api/health").status_code == 200                              # API still wins
        assert c.get("/api/nope").status_code == 404 and c.get("/api/nope").json()["detail"]
        assert c.get("/../backend/main.py").status_code in (200, 404) and "FastAPI" not in c.get("/../backend/main.py").text
    finally:
        monkeypatch.delenv("STATIC_DIR", raising=False)
        importlib.reload(main)


def test_without_a_build_the_root_is_a_plain_404(monkeypatch):
    monkeypatch.setenv("STATIC_DIR", "/definitely/not/here")
    m = importlib.reload(main)
    try:
        assert TestClient(m.app).get("/").status_code == 404
    finally:
        monkeypatch.delenv("STATIC_DIR", raising=False)
        importlib.reload(main)


def test_demo_numbers_unchanged_after_phase7():
    client.post("/api/reset?dataset=sample"); client.post("/api/analyze")
    s = client.get("/api/dashboard").json()["summary"]
    assert (round(s["expected_itc"] / 1e5, 1), round(s["reconciled_itc"] / 1e5, 1), round(s["potential_exposure"] / 1e5, 1), s["issue_count"]) == (25.0, 20.8, 4.2, 147)
