"""Phase 2 tests: reconciliation cases, matching confidence, exposure traceability, supplier aggregation."""
import pathlib
import re
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import realistic_data  # noqa: E402
import sample_data  # noqa: E402
from reconcile import (ACTION, CONF_PROBABLE, CONF_STRONG, EXPLAIN, exposure_ledger, match_confidence,  # noqa: E402
                       match_label, normalize_invoice_no, normalize_supplier_name, reconcile)

G1, G2 = "07AABCA1234A1Z5", "27AABCB5678B1Z2"


def rec(inv="A/26-27/0001", gstin=G1, name="Alpha Traders Pvt Ltd", date="2026-07-10", taxable=100_000.0,
        rate=18, **kw):
    tax = taxable * rate / 100
    row = dict(supplier_gstin=gstin, supplier_name=name, invoice_no=inv, invoice_date=date, taxable_value=taxable,
               cgst=tax / 2, sgst=tax / 2, igst=0.0)
    row.update(kw)
    return row


def run(books, portal):
    b = pd.DataFrame(books)
    b.insert(0, "invoice_id", [f"B{i}" for i in range(len(b))])
    p = pd.DataFrame(portal) if portal else pd.DataFrame(columns=list(rec().keys()))
    r = reconcile(b, p)
    return r, {i["invoice_id"]: i for i in r["invoices"]}


# ---------------------------------------------------------------- normalisation
def test_invoice_number_normalisation():
    assert normalize_invoice_no("INV/2026/0042") == normalize_invoice_no("inv-2026-42") == normalize_invoice_no("INV 2026 042")
    assert normalize_invoice_no("A/1") != normalize_invoice_no("A/2")


def test_supplier_name_normalisation():
    assert (normalize_supplier_name("Kalyani Polymers Pvt. Ltd.")
            == normalize_supplier_name("KALYANI POLYMERS PRIVATE LIMITED")
            == normalize_supplier_name("Kalyani Polymers"))
    assert normalize_supplier_name("Bhatt & Sons Co") == normalize_supplier_name("Bhatt and Sons")


# ---------------------------------------------------------------- the nine case types
def test_exact_match():
    _, inv = run([rec()], [rec()])
    i = inv["B0"]
    assert i["recon_status"] == "MATCHED" and i["exposure"] == 0
    assert i["match_method"] == "exact" and i["match_confidence"] == 100.0


def test_invoice_number_format_variation_still_matches():
    _, inv = run([rec(inv="INV/2026/0042")], [rec(inv="inv-2026-42")])
    i = inv["B0"]
    assert i["recon_status"] == "MATCHED" and i["match_method"] == "exact_normalized"
    assert CONF_PROBABLE <= i["match_confidence"] < 100          # confidence reflects the formatting difference


def test_rounding_difference_is_not_an_issue():
    p = rec()
    p["cgst"] += 1.0
    p["sgst"] += 1.0
    _, inv = run([rec()], [p])
    assert inv["B0"]["recon_status"] == "MATCHED"


def test_tax_mismatch_detected():
    # same taxable value, portal reports a lower rate
    _, inv = run([rec(rate=18)], [rec(rate=12)])
    i = inv["B0"]
    assert i["recon_status"] == "TAX_MISMATCH"
    assert i["exposure"] == pytest.approx(18_000 - 12_000)


def test_amount_mismatch_detected():
    _, inv = run([rec(taxable=100_000)], [rec(taxable=80_000)])
    i = inv["B0"]
    assert i["recon_status"] == "AMOUNT_MISMATCH"
    assert i["exposure"] == pytest.approx(18_000 - 14_400)


def test_missing_invoice_detected():
    _, inv = run([rec(), rec(inv="A/26-27/0002", date="2026-07-12")], [rec()])
    assert inv["B0"]["recon_status"] == "MATCHED"
    m = inv["B1"]
    assert m["recon_status"] == "MISSING" and m["exposure"] == pytest.approx(18_000)
    assert m["match_confidence"] is None


def test_duplicate_detected_even_with_formatting_difference():
    _, inv = run([rec(inv="INV-0042"), rec(inv="inv 42")], [rec(inv="INV-0042")])
    assert inv["B0"]["recon_status"] == "MATCHED"
    d = inv["B1"]
    assert d["recon_status"] == "DUPLICATE" and d["exposure"] == pytest.approx(18_000)
    assert d["related_invoice_id"] == "B0"


def test_supplier_name_variation_is_one_supplier():
    books = [rec(inv="A/1", name="Alpha Traders Pvt Ltd"),
             rec(inv="A/2", name="ALPHA TRADERS PRIVATE LIMITED", date="2026-07-11"),
             rec(inv="A/3", name="Alpha Traders Pvt. Ltd.", date="2026-07-12")]
    portal = [dict(b) for b in books]
    r, inv = run(books, portal)
    assert all(i["recon_status"] == "MATCHED" for i in inv.values())
    assert len(r["suppliers"]) == 1
    assert r["suppliers"][0]["invoice_count"] == 3 and len(r["suppliers"][0]["name_variants"]) == 3


def test_gstin_typo_with_name_variation_goes_to_review_with_confidence():
    typo = G1[:7] + "9" + G1[8:]
    b = rec(gstin=typo, name="ALPHA TRADERS PRIVATE LIMITED")
    _, inv = run([b], [rec()])
    i = inv["B0"]
    assert i["recon_status"] == "REVIEW" and i["issue_type"] == "GSTIN_MISMATCH"
    assert i["exposure"] == pytest.approx(18_000)
    assert 70 <= i["match_confidence"] < 100
    assert i["supplier_id"] == G1                                  # filed under the portal's GSTIN


def test_fuzzy_match_exposes_computed_confidence():
    b = rec(inv="ABC/00412", date="2026-07-10")
    p = rec(inv="ABC/00421", date="2026-07-12")                     # keying slip + 2 days apart
    _, inv = run([b], [p])
    i = inv["B0"]
    assert i["recon_status"] == "REVIEW" and i["match_method"] == "fuzzy"
    assert i["match_confidence"] == match_confidence(
        {**b, "supplier_gstin": G1}, {**p, "supplier_gstin": G1})   # nothing invented: it is the formula's output
    assert i["match_confidence_label"] == match_label(i["match_confidence"])
    assert i["exposure"] == pytest.approx(18_000)


def test_confidence_drops_as_evidence_weakens_and_labels_follow_thresholds():
    base = rec(inv="ABC/00412", date="2026-07-10")
    close = match_confidence(base, rec(inv="ABC/00421", date="2026-07-11"))
    far = match_confidence(base, rec(inv="ABC/00421", date="2026-07-16", taxable=92_000))
    assert far < close
    assert match_label(CONF_STRONG) == "Strong match"
    assert match_label(CONF_PROBABLE) == "Probable match"
    assert match_label(CONF_PROBABLE - 0.1) == "Requires review"
    assert far < CONF_PROBABLE and match_label(far) == "Requires review"
    # and the engine links it (gates pass) but labels it as requiring review
    _, inv = run([base], [rec(inv="ABC/00421", date="2026-07-16", taxable=92_000)])
    assert inv["B0"]["match_method"] == "fuzzy" and inv["B0"]["match_confidence_label"] == "Requires review"


def test_dissimilar_records_are_not_fuzzy_linked():
    # different invoice number AND different value: this must stay MISSING, not be force-matched
    _, inv = run([rec(inv="ABC/00412")], [rec(inv="XYZ/99001", taxable=40_000, date="2026-07-12")])
    assert inv["B0"]["recon_status"] == "MISSING"


def test_fuzzy_value_and_date_gates():
    base = rec(inv="ABC/00412", date="2026-07-10")
    # similar number, same supplier, but the value is far apart -> a different invoice, not a match
    _, inv = run([base], [rec(inv="ABC/00421", date="2026-07-11", taxable=50_000)])
    assert inv["B0"]["recon_status"] == "MISSING"
    # similar number and value, but a month apart -> not linked either
    _, inv = run([base], [rec(inv="ABC/00421", date="2026-08-10")])
    assert inv["B0"]["recon_status"] == "MISSING"


def test_portal_tax_higher_than_claimed_adds_no_exposure():
    r, inv = run([rec(taxable=80_000)], [rec(taxable=100_000)])
    i = inv["B0"]
    assert i["recon_status"] == "AMOUNT_MISMATCH" and i["exposure"] == 0
    assert r["summary"]["potential_exposure"] == 0


def test_high_value_flag():
    _, inv = run([rec(taxable=100_000), rec(inv="A/9", date="2026-07-15", taxable=600_000)],
                 [rec(), rec(inv="A/9", date="2026-07-15", taxable=600_000)])
    assert inv["B0"]["high_value"] is False and inv["B1"]["high_value"] is True
    assert inv["B1"]["recon_status"] == "MATCHED" and inv["B1"]["exposure"] == 0   # a prompt to look, not an issue


# ---------------------------------------------------------------- exposure + traceability
@pytest.fixture(scope="module", params=["sample", "realistic"])
def result(request):
    if request.param == "sample":
        b, p, _ = sample_data.build_sample()
    else:
        b, p, _ = realistic_data.build_realistic()
    return reconcile(b, p)


def test_exposure_is_traceable_to_invoices(result):
    ledger = exposure_ledger(result)
    s = result["summary"]
    assert sum(x["exposure"] for x in ledger) == pytest.approx(s["potential_exposure"], abs=0.5)
    assert s["expected_itc"] - s["reconciled_itc"] == pytest.approx(s["potential_exposure"], abs=0.5)
    for x in ledger:                                    # which invoice / supplier / issue / tax / why
        assert x["invoice_id"] and x["supplier_name"] and x["issue_label"] and x["reason"]
        assert x["itc_claimed"] > 0 and 0 < x["exposure"] <= x["itc_claimed"] + 0.01


def test_exposure_by_status_matches_total(result):
    sb = result["status_breakdown"]
    assert sum(x["exposure"] for x in sb) == pytest.approx(result["summary"]["potential_exposure"], abs=0.5)
    assert sum(x["count"] for x in sb) == result["summary"]["invoice_count"]


# ---------------------------------------------------------------- supplier aggregation
def test_supplier_aggregation_equals_invoice_level(result):
    inv = pd.DataFrame(result["invoices"])
    sups = pd.DataFrame(result["suppliers"]).set_index("supplier_id")
    assert sups["exposure"].sum() == pytest.approx(inv["exposure"].sum(), abs=0.5)
    assert sups["invoice_count"].sum() == len(inv)
    assert sups["affected_invoice_count"].sum() == (inv["status"] == "issue").sum()
    for sid, g in inv.groupby("supplier_id"):
        s = sups.loc[sid]
        assert s["invoice_count"] == len(g)
        assert s["affected_invoice_count"] == s["issue_count"] == (g["status"] == "issue").sum()
        assert s["total_potential_exposure"] == pytest.approx(g["exposure"].sum(), abs=0.01)
        assert s["missing_count"] == (g["recon_status"] == "MISSING").sum()
        assert s["duplicate_count"] == (g["recon_status"] == "DUPLICATE").sum()
        assert s["tax_mismatch_count"] == (g["recon_status"] == "TAX_MISMATCH").sum()
        assert s["amount_mismatch_count"] == (g["recon_status"] == "AMOUNT_MISMATCH").sum()
        assert s["mismatch_rate"] == pytest.approx(s["affected_invoice_count"] / s["invoice_count"], abs=0.001)
        assert sum(t["count"] for t in s["by_type"]) == s["affected_invoice_count"]
        assert sum(m["invoice_count"] for m in s["monthly"]) == s["invoice_count"]
        assert sum(m["exposure"] for m in s["monthly"]) == pytest.approx(s["exposure"], abs=0.01)


# ---------------------------------------------------------------- Phase 1 demo is untouched
def test_demo_sample_numbers_unchanged():
    b, p, _ = sample_data.build_sample()
    r = reconcile(b, p)
    s = r["summary"]
    assert (round(s["expected_itc"], -3), round(s["reconciled_itc"], -3), round(s["potential_exposure"], -3)) \
        == (2_500_000, 2_079_000, 421_000)
    assert s["issue_count"] == 147 and s["invoice_count"] == 620 and len(r["suppliers"]) == 28
    assert [x["supplier_name"] for x in r["suppliers"][:3]] == [
        "Sundaram Steel Traders", "Kalyani Polymers Pvt Ltd", "Raghav Logistics & Freight Pvt Ltd"]
    assert {x["type"]: x["count"] for x in r["breakdown"]} == sample_data.ISSUE_PLAN      # no fuzzy side-effects


# ---------------------------------------------------------------- realistic dataset
@pytest.fixture(scope="module")
def bundle():
    bd = realistic_data.build_bundle()
    return bd, reconcile(bd["books"], bd["portal"])


def test_realistic_dataset_is_deterministic_and_small():
    a, b = realistic_data.build_bundle(), realistic_data.build_bundle()
    assert a["books"].equals(b["books"]) and a["portal"].equals(b["portal"]) and a["truth"].equals(b["truth"])
    assert len(a["books"]) < 700 and a["suppliers"].shape[0] == 20


def test_realistic_dataset_contains_every_case_type(bundle):
    bd, _ = bundle
    assert set(bd["truth"]["case"]) >= {"EXACT", "FORMAT_VARIATION", "NAME_VARIATION", "ROUNDING", "TAX_MISMATCH",
                                        "AMOUNT_MISMATCH", "MISSING", "DELAYED_REPORTING", "DUPLICATE",
                                        "GSTIN_TYPO", "FUZZY_INVOICE"}
    assert not bd["payments"].empty


def test_engine_agrees_with_ground_truth_on_every_invoice(bundle):
    bd, r = bundle
    got = pd.DataFrame(r["invoices"]).set_index("invoice_id")
    t = bd["truth"].set_index("invoice_id")
    assert list(got.index) == list(t.index)
    assert (got["recon_status"] == t["expected_status"]).all(), t[got["recon_status"] != t["expected_status"]]
    assert (got["exposure"] - t["expected_exposure"]).abs().max() < 0.01


def test_supplier_archetypes_show_the_intended_behaviour(bundle):
    bd, r = bundle
    arche = bd["suppliers"].set_index("supplier_gstin")["archetype"]
    s = pd.DataFrame(r["suppliers"])
    s["archetype"] = s["supplier_id"].map(arche)
    by = s.groupby("archetype").agg(rate=("mismatch_rate", "mean"), n=("invoice_count", "mean"))
    assert by.loc["reliable", "rate"] < 0.08
    assert by.loc["inconsistent", "rate"] > 0.18
    assert by.loc["high_value", "n"] < by.loc[["reliable", "inconsistent", "deteriorating"], "n"].min()
    per_issue = s[s.issue_count > 0].assign(e=lambda d: d.exposure / d.issue_count).groupby("archetype")["e"].mean()
    assert per_issue["high_value"] > 3 * per_issue.drop("high_value").max()
    # deteriorating suppliers: issue rate in the last three months well above the first three
    for sup in (x for x in r["suppliers"] if arche.get(x["supplier_id"]) == "deteriorating"):
        m = sup["monthly"]
        early, late = m[:3], m[-3:]
        rate = lambda ms: sum(x["issue_count"] for x in ms) / sum(x["invoice_count"] for x in ms)  # noqa: E731
        assert rate(late) > rate(early) + 0.15, sup["supplier_name"]


# ---------------------------------------------------------------- wording
def test_no_forbidden_wording_anywhere(result):
    bad = re.compile(r"fraud|confirmed (itc )?loss|guaranteed", re.I)
    texts = list(EXPLAIN.values()) + list(ACTION.values())
    for i in result["invoices"]:
        texts += [i["explanation"], i["exposure_reason"] or "", i["suggested_action"] or ""]
    assert not [t for t in texts if bad.search(t)]


# ---------------------------------------------------------------- API
def test_api_both_datasets_and_exposure_endpoint():
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    assert c.post("/api/reset?dataset=nope").status_code == 422

    assert c.post("/api/reset").json()["dataset"] == "sample"                     # default stays the demo sample
    s = c.post("/api/analyze").json()["summary"]
    assert s["issue_count"] == 147

    assert c.post("/api/reset?dataset=realistic").json()["dataset"] == "realistic"
    assert c.get("/api/dashboard").status_code == 409                              # reset clears results
    c.post("/api/analyze")
    d = c.get("/api/dashboard").json()
    sup = c.get("/api/suppliers").json()["suppliers"][0]
    detail = c.get(f"/api/suppliers/{sup['supplier_id']}").json()
    assert detail["supplier"]["affected_invoice_count"] == len(detail["invoices"])
    inv = c.get(f"/api/invoices/{detail['invoices'][0]['invoice_id']}").json()
    assert inv["exposure_reason"] and inv["recon_status"]
    led = c.get("/api/exposure").json()
    assert led["potential_exposure"] == pytest.approx(d["summary"]["potential_exposure"], abs=0.5)
    led_s = c.get("/api/exposure", params={"supplier_id": sup["supplier_id"]}).json()
    assert led_s["potential_exposure"] == pytest.approx(sup["exposure"], abs=0.5)
    c.post("/api/reset")                                                            # leave the demo sample loaded
