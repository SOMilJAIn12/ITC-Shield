"""Phase 3B tests: exposure aggregation (total / supplier / issue type) reconciles with the invoice-level ledger."""
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import realistic_data  # noqa: E402
import sample_data  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from reconcile import CORE_EXPOSURE_CODES, exposure_aggregation, exposure_ledger, reconcile  # noqa: E402

G1, G2 = "07AABCA1234A1Z5", "27AABCB5678B1Z2"


def rec(inv, gstin=G1, name="Alpha Traders Pvt Ltd", taxable=100_000.0, date="2026-07-10", tax=None):
    t = taxable * 0.18 if tax is None else tax
    return dict(supplier_gstin=gstin, supplier_name=name, invoice_no=inv, invoice_date=date,
                taxable_value=taxable, cgst=t / 2, sgst=t / 2, igst=0.0)


@pytest.fixture(scope="module", params=["sample", "realistic"])
def result(request):
    b, p, _ = (sample_data.build_sample() if request.param == "sample" else realistic_data.build_realistic())
    return reconcile(b, p)


def cents(x):
    return round(x * 100)


# ------------------------------------------------------------------ invariants (both datasets)
def test_total_equals_sum_of_invoices(result):
    agg = exposure_aggregation(result)
    from_invoices = sum(cents(i["exposure"]) for i in result["invoices"] if i["exposure"] > 0)   # raw invoice rows, not the ledger
    assert cents(agg["total_exposure"]) == from_invoices
    assert cents(agg["total_exposure"]) == sum(cents(x["exposure"]) for x in agg["items"])
    assert agg["total_exposure"] == pytest.approx(result["summary"]["potential_exposure"], abs=0.01)   # <= 1 paisa float rounding
    assert agg["invoice_count"] == len(agg["items"]) == sum(1 for i in result["invoices"] if i["exposure"] > 0)


def test_sum_of_suppliers_equals_total(result):
    agg = exposure_aggregation(result)
    assert sum(cents(s["total_potential_exposure"]) for s in agg["by_supplier"]) == cents(agg["total_exposure"])
    assert sum(s["affected_invoice_count"] for s in agg["by_supplier"]) == agg["invoice_count"]
    assert sum(s["exposure_share_pct"] for s in agg["by_supplier"]) == pytest.approx(100, abs=0.5 + 0.05 * len(agg["by_supplier"]))


def test_issue_type_aggregation_reconciles_with_ledger(result):
    agg = exposure_aggregation(result)
    types = {t["reason_code"]: t for t in agg["by_issue_type"]}
    assert set(CORE_EXPOSURE_CODES) <= types.keys()                       # the four headline types are always present
    assert sum(cents(t["exposure"]) for t in agg["by_issue_type"]) == cents(agg["total_exposure"])
    assert sum(t["count"] for t in agg["by_issue_type"]) == agg["invoice_count"]
    for code, t in types.items():                                         # each type equals the ledger rows with that code
        rows = [x for x in exposure_ledger(result) if x["reason_code"] == code]
        assert t["count"] == len(rows)
        assert cents(t["exposure"]) == sum(cents(x["exposure"]) for x in rows)


def test_zero_exposure_invoices_excluded(result):
    agg = exposure_aggregation(result)
    assert all(x["exposure"] > 0 for x in agg["items"])
    zero_ids = {i["invoice_id"] for i in result["invoices"] if i["status"] == "issue" and i["exposure"] == 0}
    assert not zero_ids & {x["invoice_id"] for x in agg["items"]}
    for s in agg["by_supplier"]:
        assert s["affected_invoice_count"] > 0 and s["total_potential_exposure"] > 0


def test_supplier_counts_add_up_and_match_invoices(result):
    agg = exposure_aggregation(result)
    for s in agg["by_supplier"]:
        parts = sum(v for k, v in s.items() if k.endswith("_count") and k != "affected_invoice_count")
        assert parts == s["affected_invoice_count"]
    inv = pd.DataFrame(result["invoices"])
    exposed = inv[inv["exposure"] > 0]
    for s in agg["by_supplier"]:                                           # independent recompute from invoice rows
        g = exposed[exposed["supplier_id"] == s["supplier_id"]]
        assert s["affected_invoice_count"] == len(g)
        assert cents(s["total_potential_exposure"]) == sum(cents(v) for v in g["exposure"])
        assert s["missing_count"] == int((g["reason_code"] == "MISSING").sum())
        assert s["duplicate_count"] == int((g["reason_code"] == "DUPLICATE").sum())


# ------------------------------------------------------------------ one supplier checked by hand
def test_supplier_manual_check():
    books = [rec("A1", tax=18_000),                                        # TAX_MISMATCH: portal 16,200 -> 1,800
             rec("A2", taxable=50_000),                                    # MISSING: 9,000
             rec("A3", taxable=40_000),                                    # AMOUNT_MISMATCH: portal 30,000 value -> 7,200 vs 5,400 = 1,800
             rec("A4"), rec("a-4"),                                        # A4 matched; second entry is DUPLICATE: 18,000
             rec("A5", taxable=20_000),                                    # portal GST higher -> zero exposure, excluded
             rec("B1", gstin=G2, name="Beta Metals", taxable=10_000)]      # other supplier, MISSING: 1,800
    portal = [rec("A1", tax=16_200), rec("A3", taxable=30_000), rec("A4"),
              rec("A5", taxable=20_000, tax=4_000)]
    b = pd.DataFrame(books)
    b.insert(0, "invoice_id", [f"B{i}" for i in range(len(b))])
    r = reconcile(b, pd.DataFrame(portal))
    agg = exposure_aggregation(r)
    alpha = next(s for s in agg["by_supplier"] if s["supplier_id"] == G1)
    assert alpha["supplier_name"] == "Alpha Traders Pvt Ltd"
    assert alpha["affected_invoice_count"] == 4                            # A5 (zero exposure) not counted
    assert (alpha["tax_mismatch_count"], alpha["amount_mismatch_count"],
            alpha["missing_count"], alpha["duplicate_count"]) == (1, 1, 1, 1)
    assert alpha["total_potential_exposure"] == pytest.approx(1_800 + 1_800 + 9_000 + 18_000)
    assert agg["total_exposure"] == pytest.approx(30_600 + 1_800)
    types = {t["reason_code"]: t for t in agg["by_issue_type"]}
    assert types["MISSING"]["exposure"] == pytest.approx(9_000 + 1_800) and types["MISSING"]["count"] == 2
    assert types["DUPLICATE"]["exposure"] == pytest.approx(18_000)
    assert types["TAX_MISMATCH"]["exposure"] == pytest.approx(1_800)
    assert types["AMOUNT_MISMATCH"]["exposure"] == pytest.approx(1_800)


# ------------------------------------------------------------------ API + demo safety
def test_api_exposure_payload_and_backwards_compat():
    import main
    c = TestClient(main.app)
    c.post("/api/reset", params={"dataset": "sample"})
    c.post("/api/analyze")
    d = c.get("/api/exposure").json()
    assert {"potential_exposure", "count", "items"} <= d.keys()           # Phase 2/3A keys preserved
    assert {"total_exposure", "by_supplier", "by_issue_type"} <= d.keys()
    assert d["potential_exposure"] == pytest.approx(d["total_exposure"], abs=0.01)
    assert d["count"] == len(d["items"])
    assert cents(d["total_exposure"]) == sum(cents(s["total_potential_exposure"]) for s in d["by_supplier"])
    assert cents(d["total_exposure"]) == sum(cents(t["exposure"]) for t in d["by_issue_type"])
    sid = d["by_supplier"][0]["supplier_id"]
    ds = c.get("/api/exposure", params={"supplier_id": sid}).json()      # supplier filter scopes every aggregate
    assert len(ds["by_supplier"]) == 1 and ds["total_exposure"] == d["by_supplier"][0]["total_potential_exposure"]
    dash = c.get("/api/dashboard").json()
    assert d["total_exposure"] == pytest.approx(dash["summary"]["potential_exposure"], abs=0.01)
    assert round(dash["summary"]["potential_exposure"] / 1e5, 1) == 4.2


def test_demo_numbers_unchanged():
    b, p, _ = sample_data.build_sample()
    r = reconcile(b, p)
    s = r["summary"]
    assert (round(s["expected_itc"] / 1e5, 1), round(s["reconciled_itc"] / 1e5, 1),
            round(s["potential_exposure"] / 1e5, 1), s["issue_count"]) == (25.0, 20.8, 4.2, 147)
