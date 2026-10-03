"""Phase 3A tests: Potential ITC Exposure is traceable per invoice, with deterministic reason codes."""
import pathlib
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import sample_data  # noqa: E402
from reconcile import _inr, exposure_ledger, reconcile  # noqa: E402

G1 = "07AABCA1234A1Z5"
LEDGER_KEYS = {"invoice_id", "supplier", "purchase_taxable_value", "purchase_gst", "portal_taxable_value",
               "portal_gst", "reconciliation_status", "matching_confidence", "exposure", "reason_code", "reason"}


def rec(inv="A/26-27/0001", taxable=100_000.0, rate=18, gstin=G1, date="2026-07-10"):
    tax = taxable * rate / 100
    return dict(supplier_gstin=gstin, supplier_name="Alpha Traders Pvt Ltd", invoice_no=inv, invoice_date=date,
                taxable_value=taxable, cgst=tax / 2, sgst=tax / 2, igst=0.0)


def run(books, portal):
    b = pd.DataFrame(books)
    b.insert(0, "invoice_id", [f"B{i}" for i in range(len(b))])
    p = pd.DataFrame(portal) if portal else pd.DataFrame(columns=list(rec().keys()))
    r = reconcile(b, p)
    return r, {x["invoice_id"]: x for x in exposure_ledger(r)}, {x["invoice_id"]: x for x in r["invoices"]}


def test_exposed_invoice_has_correct_amount():
    # purchase GST 18,000 vs portal 16,200 (same taxable value) -> exposure is exactly the 1,800 gap
    r, ledger, _ = run([rec()], [{**rec(), "cgst": 8_100.0, "sgst": 8_100.0}])
    x = ledger["B0"]
    assert LEDGER_KEYS <= x.keys()
    assert x["purchase_gst"] == 18_000 and x["portal_gst"] == 16_200
    assert x["purchase_taxable_value"] == x["portal_taxable_value"] == 100_000
    assert x["exposure"] == pytest.approx(1_800)
    assert r["summary"]["potential_exposure"] == pytest.approx(1_800)


def test_exposed_invoice_has_correct_reason():
    _, ledger, _ = run([rec()], [{**rec(), "cgst": 8_100.0, "sgst": 8_100.0}])
    x = ledger["B0"]
    assert x["reason_code"] == "TAX_MISMATCH" and x["reconciliation_status"] == "TAX_MISMATCH"
    assert x["reason"].startswith("Purchase GST is ₹18,000 while the portal record reports ₹16,200.")

    _, ledger, _ = run([rec(taxable=100_000)], [rec(taxable=90_000)])
    assert ledger["B0"]["reason_code"] == "AMOUNT_MISMATCH"
    assert "₹1,00,000" in ledger["B0"]["reason"] and "₹90,000" in ledger["B0"]["reason"]

    _, ledger, _ = run([rec(inv="X/1"), rec(inv="X/2", taxable=50_000)], [rec(inv="X/2", taxable=50_000)])
    assert ledger["B0"]["reason_code"] == "MISSING"
    assert ledger["B0"]["portal_gst"] is None and ledger["B0"]["portal_taxable_value"] is None
    assert "X/1" in ledger["B0"]["reason"] and "₹18,000" in ledger["B0"]["reason"]

    _, ledger, _ = run([rec(), rec(inv="a-26-27-1")], [rec()])      # same invoice keyed twice
    assert ledger["B1"]["reason_code"] == "DUPLICATE" and "B0" in ledger["B1"]["reason"]


def test_matched_invoice_has_zero_exposure():
    r, ledger, invs = run([rec()], [rec()])
    assert ledger == {}
    assert invs["B0"]["recon_status"] == "MATCHED" and invs["B0"]["exposure"] == 0
    assert invs["B0"]["reason_code"] is None and invs["B0"]["reason"] is None
    # rounding inside tolerance is still not an exposure
    _, ledger, invs = run([rec()], [{**rec(), "cgst": 9_002.0, "sgst": 9_000.0}])
    assert ledger == {} and invs["B0"]["exposure"] == 0


def test_portal_higher_than_purchase_adds_no_exposure():
    _, ledger, invs = run([rec()], [{**rec(), "cgst": 9_500.0, "sgst": 9_500.0}])
    assert ledger == {}
    assert invs["B0"]["reason_code"] == "TAX_MISMATCH" and "no exposure is counted" in invs["B0"]["reason"]


def test_exposure_derived_from_actual_data():
    # change the inputs -> amount and wording change with them (nothing hard-coded)
    for taxable, portal_cgst in [(200_000.0, 14_000.0), (73_500.0, 5_000.0)]:
        tax = taxable * 0.18
        portal_tax = portal_cgst * 2
        _, ledger, _ = run([rec(taxable=taxable)], [{**rec(taxable=taxable), "cgst": portal_cgst, "sgst": portal_cgst}])
        x = ledger["B0"]
        assert x["exposure"] == pytest.approx(round(tax - portal_tax, 2))
        assert _inr(tax) in x["reason"] and _inr(portal_tax) in x["reason"]
    assert _inr(1234567) == "₹12,34,567" and _inr(18000) == "₹18,000" and _inr(1250.5) == "₹1,250.50"


@pytest.fixture(scope="module")
def demo():
    b, p, _ = sample_data.build_sample()
    return reconcile(b, p)


def test_demo_numbers_unchanged_and_ledger_reconciles(demo):
    s = demo["summary"]
    assert round(s["expected_itc"] / 1e5, 1) == 25.0 and round(s["reconciled_itc"] / 1e5, 1) == 20.8
    assert round(s["potential_exposure"] / 1e5, 1) == 4.2 and s["issue_count"] == 147
    ledger = exposure_ledger(demo)
    assert sum(x["exposure"] for x in ledger) == pytest.approx(s["potential_exposure"], abs=0.5)
    inv = {i["invoice_id"]: i for i in demo["invoices"]}
    for x in ledger:
        assert LEDGER_KEYS <= x.keys() and x["reason_code"] and x["reason"]
        assert x["exposure"] > 0 and x["exposure"] <= x["purchase_gst"] + 0.01
        assert inv[x["invoice_id"]]["exposure"] == x["exposure"]
        if x["reason_code"] in ("TAX_MISMATCH", "AMOUNT_MISMATCH"):      # exposure = purchase GST - portal GST
            assert x["exposure"] == pytest.approx(x["purchase_gst"] - x["portal_gst"], abs=0.01)
        if x["reason_code"] in ("MISSING", "DUPLICATE"):
            assert x["exposure"] == pytest.approx(x["purchase_gst"], abs=0.01)
