"""Post-Phase-8 enhancement 1/2: real user-data upload (CSV / XLSX / JSON), column normalisation, isolation, and the demo_upload_data files."""
import io
import json
import os

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import main
import upload_normalize as un

client = TestClient(main.app, raise_server_exceptions=False)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEMO_DIR = os.path.join(ROOT, "demo_upload_data")
PURCHASE_CSV = os.path.join(DEMO_DIR, "sample_purchase_register.csv")
PORTAL_CSV = os.path.join(DEMO_DIR, "sample_portal_data.csv")

# Hand-computed from the two CSV files (see demo_upload_data/README.md)
EXPECTED = {"expected_itc": 422_820.0, "reconciled_itc": 373_150.0, "potential_exposure": 49_670.0, "issue_count": 6, "invoice_count": 24, "matched_count": 18}
CASES = {"ASI/26-27/0112": ("TAX_MISMATCH", 2_880.0), "PPL/26-27/0345": ("TAX_MISMATCH", 1_700.0), "SLS/26-27/0781": ("AMOUNT_MISMATCH", 3_240.0),
         "NEC/26-27/0456": ("MISSING", 12_330.0), "OCL/26-27/0223": ("DUPLICATE", 9_720.0), "ASI/26-27/0130": ("REVIEW", 19_800.0)}


@pytest.fixture(autouse=True)
def _restore():
    yield
    client.post("/api/reset?dataset=sample")


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _files(purchase, portal, p_name="purchase.csv", g_name="portal.csv"):
    return {"purchase_file": (p_name, io.BytesIO(purchase), "application/octet-stream"),
            "portal_file": (g_name, io.BytesIO(portal), "application/octet-stream")}


def _upload(purchase, portal, **kw):
    return client.post("/api/upload", files=_files(purchase, portal, **kw))


def _to_xlsx(csv_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    pd.read_csv(io.BytesIO(csv_bytes)).to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def _to_json(csv_bytes: bytes) -> bytes:
    return json.dumps(pd.read_csv(io.BytesIO(csv_bytes)).to_dict(orient="records")).encode()


# ------------------------------------------------------------------------------------ the shipped test files, end to end
def test_demo_upload_files_exist_and_are_labelled_synthetic():
    assert os.path.isfile(PURCHASE_CSV) and os.path.isfile(PORTAL_CSV)
    readme = open(os.path.join(DEMO_DIR, "README.md"), encoding="utf-8").read()
    assert "SYNTHETIC" in readme and "sample_purchase_register.csv" in readme and "sample_portal_data.csv" in readme


def test_demo_upload_csvs_through_the_whole_pipeline():
    r = _upload(_bytes(PURCHASE_CSV), _bytes(PORTAL_CSV), p_name="sample_purchase_register.csv", g_name="sample_portal_data.csv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purchase_rows"] == 24 and body["portal_rows"] == 22 and body["source"] == "upload"
    assert body["upload"]["purchase"]["mapping"]["Supplier GSTIN"] == "supplier_gstin"
    assert body["upload"]["portal"]["mapping"]["GSTIN"] == "supplier_gstin" and body["upload"]["portal"]["mapping"]["Invoice Number"] == "invoice_no"
    assert client.post("/api/analyze").status_code == 200
    s = client.get("/api/dashboard").json()["summary"]
    for k, v in EXPECTED.items():
        assert s[k] == v, (k, s[k], v)
    issues = {i["invoice_no"]: i for i in client.get("/api/invoices?status=issue").json()["invoices"]}
    assert set(issues) == set(CASES)
    for inv_no, (status, exp) in CASES.items():
        assert issues[inv_no]["recon_status"] == status and issues[inv_no]["exposure"] == exp, inv_no
    review = client.get(f"/api/invoices/{issues['ASI/26-27/0130']['invoice_id']}").json()
    assert review["portal_invoice_no"] == "ASI/26-27/0131" and review["match_confidence"] > 90
    matched = {i["invoice_no"] for i in client.get("/api/invoices?status=matched").json()["invoices"]}
    assert "PPL-26-27-0360" in matched                                   # format variation is normalised, not flagged
    # exposure ledger, radar, supplier + invoice detail and AI brief all work on uploaded data
    ex = client.get("/api/exposure").json()
    assert ex["total_exposure"] == EXPECTED["potential_exposure"] and len(ex["items"]) == 6
    p = client.get("/api/priority").json()
    assert [x["supplier_name"] for x in p["suppliers"][:2]] == ["Arvind Steel Industries", "Neha Electricals & Cables"]
    assert p["suppliers"][0]["potential_exposure"] == 22_680.0 and p["suppliers"][0]["priority_level"] == "HIGH" and p["risk_available"] is False
    sup = client.get(f"/api/suppliers/{p['suppliers'][0]['supplier_id']}").json()
    assert sup["supplier"]["investigation"]["rank"] == 1 and len(sup["invoices"]) == 2
    brief = client.post("/api/ai/investigation-brief", json={"supplier_id": p["suppliers"][0]["supplier_id"]}).json()
    assert brief["source"] == "fallback" and "Arvind Steel Industries" in brief["brief"]["draft_followup"]
    assert "ASI/26-27/0130" in brief["brief"]["draft_followup"]
    ibrief = client.post("/api/ai/investigation-brief", json={"invoice_id": issues["NEC/26-27/0456"]["invoice_id"]}).json()
    assert ibrief["brief"]["why_flagged"][0].startswith("Reconciliation status: Missing in portal")
    h = client.get("/api/health").json()
    assert h["source"] == "upload" and h["source_label"] == "Your uploaded data" and h["upload"]["purchase"]["filename"] == "sample_purchase_register.csv"


# ------------------------------------------------------------------------------------ formats
def test_xlsx_upload_gives_the_same_result_as_csv():
    r = _upload(_to_xlsx(_bytes(PURCHASE_CSV)), _to_xlsx(_bytes(PORTAL_CSV)), p_name="purchase.xlsx", g_name="portal.xlsx")
    assert r.status_code == 200, r.text
    assert r.json()["upload"]["purchase"]["format"] == "xlsx"
    client.post("/api/analyze")
    s = client.get("/api/dashboard").json()["summary"]
    assert s["potential_exposure"] == EXPECTED["potential_exposure"] and s["issue_count"] == 6


def test_xlsx_detected_by_content_even_with_wrong_extension():
    r = _upload(_to_xlsx(_bytes(PURCHASE_CSV)), _bytes(PORTAL_CSV), p_name="purchase.csv")
    assert r.status_code == 200 and r.json()["upload"]["purchase"]["format"] == "xlsx"


def test_json_array_upload():
    r = _upload(_to_json(_bytes(PURCHASE_CSV)), _to_json(_bytes(PORTAL_CSV)), p_name="purchase.json", g_name="portal.json")
    assert r.status_code == 200, r.text
    assert r.json()["upload"]["portal"]["format"] == "json"
    client.post("/api/analyze")
    assert client.get("/api/dashboard").json()["summary"]["potential_exposure"] == EXPECTED["potential_exposure"]


def test_json_gstr2b_style_nesting_is_flattened():
    portal = pd.read_csv(PORTAL_CSV)
    b2b = []
    for gstin, g in portal.groupby("GSTIN"):
        inv = []
        for _, r in g.iterrows():
            inv.append({"inum": r["Invoice Number"], "idt": pd.Timestamp(r["Invoice Date"]).strftime("%d-%m-%Y"), "val": float(r["Taxable Value"]) * 1.18,
                        "items": [{"num": 1, "rt": 18, "txval": float(r["Taxable Value"]) / 2, "igst": float(r["IGST"]) / 2, "cgst": float(r["CGST"]) / 2, "sgst": float(r["SGST"]) / 2},
                                  {"num": 2, "rt": 18, "txval": float(r["Taxable Value"]) / 2, "igst": float(r["IGST"]) / 2, "cgst": float(r["CGST"]) / 2, "sgst": float(r["SGST"]) / 2}]})
        b2b.append({"ctin": gstin, "trdnm": g["Supplier Name"].iloc[0], "inv": inv})
    doc = {"data": {"gstin": "07AABCV4821K1Z5", "rtnprd": "092026", "docdata": {"b2b": b2b}}}
    r = _upload(_bytes(PURCHASE_CSV), json.dumps(doc).encode(), g_name="gstr2b.json")
    assert r.status_code == 200, r.text
    m = r.json()["upload"]["portal"]["mapping"]
    assert m["ctin"] == "supplier_gstin" and m["inum"] == "invoice_no" and m["idt"] == "invoice_date" and m["txval"] == "taxable_value"
    client.post("/api/analyze")
    s = client.get("/api/dashboard").json()["summary"]
    assert s["potential_exposure"] == EXPECTED["potential_exposure"] and s["issue_count"] == 6


# ------------------------------------------------------------------------------------ column normalisation
def test_alias_mapping_money_formats_and_day_first_dates():
    purchase = ("Vendor Name,GSTIN of Supplier,Bill No,Date,Taxable Amount,Central Tax,State Tax,Integrated Tax\n"
                "Alpha Traders,27AAACA1111A1Z5,B-1,15/07/2026,\"₹1,00,000.00\",\"9,000\",\"9,000\",\n"
                "Alpha Traders,27AAACA1111A1Z5,B-2,16-Jul-2026,\"Rs 50,000\",,,\"9,000\"\n")
    portal = ("ctin,inum,idt,txval,iamt,camt,samt\n27AAACA1111A1Z5,B-1,2026-07-15,100000,0,9000,9000\n27AAACA1111A1Z5,B-2,2026-07-16,50000,9000,0,0\n")
    books, portal_df, meta = un.load_upload(("p.csv", purchase.encode()), ("g.csv", portal.encode()))
    assert list(books.columns) == ["supplier_name", "supplier_gstin", "invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst"]
    assert books["invoice_date"].tolist() == ["2026-07-15", "2026-07-16"]
    assert books["taxable_value"].tolist() == [100000.0, 50000.0] and books["cgst"].tolist() == [9000.0, 0.0] and books["igst"].tolist() == [0.0, 9000.0]
    assert meta["upload"]["purchase"]["mapping"]["Bill No"] == "invoice_no" and meta["period"] == "Jul 2026"
    r = _upload(purchase.encode(), portal.encode())
    assert r.status_code == 200
    client.post("/api/analyze")
    assert client.get("/api/dashboard").json()["summary"]["issue_count"] == 0


def test_total_gst_only_is_accepted_with_a_note():
    purchase = "Supplier GSTIN,Invoice No,Invoice Date,Taxable Value,Total GST\n27AAACA1111A1Z5,T-1,2026-07-01,10000,1800\n"
    portal = "GSTIN,Invoice No,Invoice Date,Taxable Value,IGST,CGST,SGST\n27AAACA1111A1Z5,T-1,2026-07-01,10000,1800,0,0\n"
    books, _, meta = un.load_upload(("p.csv", purchase.encode()), ("g.csv", portal.encode()))
    assert books["igst"].tolist() == [1800.0] and any("Total GST" in n for n in meta["upload"]["purchase"]["notes"])


def test_ambiguous_columns_are_an_error_not_a_guess():
    purchase = "GSTIN,Supplier GSTIN,Invoice No,Invoice Date,Taxable Value,IGST\n27AAACA1111A1Z5,27AAACA1111A1Z5,A-1,2026-07-01,100,18\n"
    r = _upload(purchase.encode(), _bytes(PORTAL_CSV))
    assert r.status_code == 422 and "Ambiguous columns" in r.json()["detail"] and "Keep one of them" in r.json()["detail"]


def test_missing_required_columns_message_lists_accepted_names():
    purchase = "Supplier GSTIN,Invoice No,Invoice Date,IGST\n27AAACA1111A1Z5,A-1,2026-07-01,18\n"
    r = _upload(purchase.encode(), _bytes(PORTAL_CSV))
    assert r.status_code == 422
    d = r.json()["detail"]
    assert "missing columns" in d and "taxable_value" in d and "accepted names" in d and "taxable value" in d


def test_no_tax_columns_is_an_error():
    purchase = "Supplier GSTIN,Invoice No,Invoice Date,Taxable Value\n27AAACA1111A1Z5,A-1,2026-07-01,100\n"
    r = _upload(purchase.encode(), _bytes(PORTAL_CSV))
    assert r.status_code == 422 and "no tax columns" in r.json()["detail"]


def test_bad_dates_and_bad_numbers_are_reported():
    purchase = "Supplier GSTIN,Invoice No,Invoice Date,Taxable Value,IGST\n27AAACA1111A1Z5,A-1,31/31/2026,100,18\n"
    r = _upload(purchase.encode(), _bytes(PORTAL_CSV))
    assert r.status_code == 422 and "unreadable date" in r.json()["detail"] and "31/31/2026" in r.json()["detail"]
    purchase = "Supplier GSTIN,Invoice No,Invoice Date,Taxable Value,IGST\n27AAACA1111A1Z5,A-1,2026-07-01,abc,18\n"
    r = _upload(purchase.encode(), _bytes(PORTAL_CSV))
    assert r.status_code == 422 and "taxable value" in r.json()["detail"] and "abc" in r.json()["detail"]


def test_blank_rows_ignored_and_blank_gstin_rejected():
    purchase = "Supplier GSTIN,Invoice No,Invoice Date,Taxable Value,IGST\n27AAACA1111A1Z5,A-1,2026-07-01,100,18\n,,,,\n"
    books, _, meta = un.load_upload(("p.csv", purchase.encode()), ("g.csv", _bytes(PORTAL_CSV)))
    assert len(books) == 1                                                            # the fully blank line is dropped
    purchase = "Supplier GSTIN,Invoice No,Invoice Date,Taxable Value,IGST\n,A-1,2026-07-01,100,18\n"
    with pytest.raises(ValueError, match="blank Supplier GSTIN"):
        un.load_upload(("p.csv", purchase.encode()), ("g.csv", _bytes(PORTAL_CSV)))


# ------------------------------------------------------------------------------------ malformed / empty
@pytest.mark.parametrize("payload,name,needle", [
    (b"", "p.csv", "is empty"),
    (b"   \n", "p.csv", "is empty"),
    (b"%PDF-1.4 binary \xff\xfe junk", "p.pdf", "CSV"),
    (b"Supplier GSTIN,Invoice No,Invoice Date,Taxable Value,IGST\n", "p.csv", "no invoices"),
    (b"{not json", "p.json", "not valid JSON"),
    (b'{"meta": {"x": 1}}', "p.json", "list of invoices"),
    (b"just one line of prose", "p.csv", "does not look like a CSV"),
])
def test_malformed_or_empty_files_give_clear_errors(payload, name, needle):
    r = _upload(payload, _bytes(PORTAL_CSV), p_name=name)
    assert r.status_code == 422, r.text
    assert needle in r.json()["detail"] and "Traceback" not in r.text


def test_old_xls_is_refused_with_advice():
    r = _upload(b"\xd0\xcf\x11\xe0 old binary workbook", _bytes(PORTAL_CSV), p_name="book.xls")
    assert r.status_code == 422 and ".xlsx or .csv" in r.json()["detail"]


# ------------------------------------------------------------------------------------ isolation from the sample datasets
def test_uploaded_data_is_isolated_from_the_sample_datasets():
    client.post("/api/reset?dataset=sample"); client.post("/api/analyze")
    demo = client.get("/api/dashboard").json()["summary"]
    assert demo["issue_count"] == 147
    assert _upload(_bytes(PURCHASE_CSV), _bytes(PORTAL_CSV)).status_code == 200
    assert client.get("/api/dashboard").status_code == 409                       # upload clears results; nothing analysed yet
    client.post("/api/analyze")
    up = client.get("/api/dashboard").json()
    assert up["summary"]["issue_count"] == 6 and up["company"] == "Your uploaded data" and up["source"] == "upload"
    assert {s["supplier_name"] for s in client.get("/api/suppliers").json()["suppliers"]} == {
        "Arvind Steel Industries", "Prakash Packaging Pvt Ltd", "Sunrise Logistics Services", "Neha Electricals & Cables", "Om Chemicals Ltd"}
    client.post("/api/reset?dataset=sample"); client.post("/api/analyze")
    again = client.get("/api/dashboard").json()["summary"]
    assert again == demo                                                             # the demo numbers are untouched by the upload
    client.post("/api/reset?dataset=realistic"); client.post("/api/analyze")
    assert client.get("/api/dashboard").json()["summary"]["issue_count"] == 85
    assert client.get("/api/health").json()["source"] == "realistic"


def test_health_and_schema_endpoints_describe_sources():
    h = client.get("/api/health").json()
    assert h["source"] == "sample" and "synthetic" in h["source_label"].lower() and h["upload"] is None
    assert set(h["dataset_labels"]) == {"sample", "realistic"} and all("synthetic" in v.lower() for v in h["dataset_labels"].values())
    s = client.get("/api/upload/schema").json()
    assert s["formats"] == ["csv", "xlsx", "json"] and s["required"] == un.REQUIRED and "gstin" in s["aliases"]["supplier_gstin"]
